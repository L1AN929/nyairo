"""v0.4 B：来访接进正常生活 —— 普通来话只观察、邀请一次真 Decision、共处有结束、之后自己决定续作。

两类用例，各自回答不同的问题：

* ``SocialContactPortDispatchTest``（纯单进程，不建 runtime）：A 的 transport 账本交给
  ``SocialContactPort`` 之后，**每条事件恰好一个终态**、消息永不 pause/start、DEFER 具名且
  不在 ``pending()`` 里重复问、失败留成具名 UNKNOWN 不做盲重做、视图里没有正文。
* ``SocialLifeChainTest``（真 Supply/World/runtime/assembly/``runner_tick``）：只把**模型**
  换成本地固定的原始 JSON 回答（``StubCognition`` 家族，与 D 阶段同一套夹具），事件由 A 的
  真 ``LocalContactInbox`` 投递。整链是：阅读→普通来话观察→邀请一次 Decision 接受→原阅读在
  同一 Activity 的同一页 park→joint→显式 end→新 Decision RESUME 原阅读并读第二页。

``SocialLifeChainTest`` 继承 ``DScenarioBase``：父级（整链验收）可以直接复用这里的
``_social_fixture()`` / ``_tick()`` / ``_submit()`` 与 ``INVITATION``，不需要再自己搭一套。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path

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

import life_social_contacts as social  # noqa: E402

#: 整链那一段复用 D 阶段的真夹具（``DScenarioBase``），而它和它的 runtime 依赖 ``fcntl``：
#: 在没有 ``fcntl`` 的平台上这些名字不可用。分派用例如常跑，整链用例**具名跳过**（不是冒充通过）。
CHAIN_IMPORT_ERROR: str | None = None
try:
    from test_life_v1_integration import DScenarioBase, posix_only  # noqa: E402
    from test_one_day_life import StubCognition  # noqa: E402

    import autonomous_life_assembly as assembly  # noqa: E402
    import life_local_contact as lc  # noqa: E402
    import one_day_life as odl  # noqa: E402
except Exception as _chain_exc:  # pragma: no cover - a platform without the POSIX owners
    CHAIN_IMPORT_ERROR = "%s: %s" % (type(_chain_exc).__name__, _chain_exc)
    DScenarioBase = object  # type: ignore[assignment]
    StubCognition = object  # type: ignore[assignment]
    assembly = lc = odl = None  # type: ignore[assignment]

    def posix_only(target):  # type: ignore[misc]
        return unittest.skip("the chain fixture needs the POSIX owners (%s)" % CHAIN_IMPORT_ERROR)(
            target)

BODY_MARKER = "私人正文不该出现在任何汇总里"
SCOPE = "一起坐一会儿"


# =====================================================================================
# 1. 端口分派（不建 runtime：账本与协议都是替身）
# =====================================================================================


class FakeLedger:
    """A's transport ledger, in memory, with the same shape the port consumes."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.order: list[str] = []
        self.operations: dict[str, str] = {}
        self.recorded: list[dict] = []

    def add(self, kind: str, *, invitation: dict | None = None, end: dict | None = None,
            body: str | None = None, operation_key: str | None = None) -> str:
        ref = "contact_evt:%04d" % len(self.order)
        self.events[ref] = {
            "event_ref": ref, "event_kind": kind, "processing_state": "PENDING",
            "invitation": invitation, "end": end, "body": body, "results": [],
            "operation_key": operation_key or ("contact_op:%04d" % len(self.order)),
        }
        self.order.append(ref)
        self.operations[self.events[ref]["operation_key"]] = ref
        return ref

    # -- the read面 the port uses ---
    def _rows(self, state: str, limit=None) -> list[dict]:
        rows = [dict(self.events[ref]) for ref in self.order
                if self.events[ref]["processing_state"] == state]
        return rows if limit is None else rows[:int(limit)]

    def pending(self, *, limit=None) -> dict:
        rows = self._rows("PENDING")
        shown = rows if limit is None else rows[:int(limit)]
        return {"ok": True, "status": "ok", "data": {"events": shown, "matched": len(rows),
                                                     "ledger_state": "OK"}}

    def summary(self) -> dict:
        counts = {state: len(self._rows(state)) for state in
                  ("PENDING", "CLAIMED", "DEFERRED", "UNKNOWN", "PROCESSED")}
        return {"ok": True, "data": {"counts": counts, "by_kind": {}, "event_count": len(self.order),
                                     "bodies_stored": sum(1 for row in self.events.values()
                                                          if row.get("body")),
                                     "ledger_state": "OK"}}

    def operation(self, key: str) -> dict:
        ref = self.operations.get(str(key))
        if ref is None:
            return {"ok": False, "status": "absent", "data": None}
        return {"ok": True, "status": "applied", "data": {"event": dict(self.events[ref])}}

    # -- the write面 the port uses ---
    def claim(self, ref: str, *, consumer: str, now=None, operation_key=None) -> dict:
        row = self.events[ref]
        if row["processing_state"] == "PROCESSED":
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_EVENT_ALREADY_PROCESSED"}
        row["processing_state"] = "CLAIMED"
        row["claim_token"] = "contact_claim:%s" % ref[-4:]
        return {"ok": True, "status": "applied", "claim_token": row["claim_token"]}

    def get_event(self, ref: str, *, include_body: bool = False) -> dict:
        row = dict(self.events[ref])
        row["body"] = row["body"] if include_body else None
        return {"ok": True, "data": {"event": row}}

    def record_result(self, ref: str, *, outcome: str, claim_token=None, reason_code: str = "",
                      detail: str = "", session_ref=None, invitation_ref=None,
                      decision_ref=None, defer_condition=None, opportunity_ref=None,
                      owner_refs=None, operation_key=None, now=None) -> dict:
        row = self.events[ref]
        result = {"outcome": outcome, "reason_code": reason_code or None,
                  "session_ref": session_ref, "invitation_ref": invitation_ref,
                  "decision_ref": decision_ref, "defer_condition": defer_condition}
        row["results"].append(result)
        row["result"] = result
        row["processing_state"] = {"OBSERVED": "PROCESSED", "ACCEPTED": "PROCESSED",
                                   "DECLINED": "PROCESSED", "ENDED": "PROCESSED",
                                   "DEFERRED": "DEFERRED", "UNKNOWN": "UNKNOWN"}[outcome]
        self.recorded.append(dict(result, event_ref=ref))
        return {"ok": True, "status": "applied", "processing_state": row["processing_state"],
                "reason_code": "", "writes_performed": 1}


class FakeProtocol:
    """The finite co-presence protocol, answering with fixtures instead of owners."""

    def __init__(self, *, begin_result: dict | None = None, end_result: dict | None = None,
                 session: dict | None = None) -> None:
        self.begin_result = dict(begin_result or {})
        self.end_result = dict(end_result or {})
        self.session = dict(session or {"state": "NONE"})
        self.begin_calls: list[str] = []
        self.end_calls: list[str] = []
        self.observed: list[dict] = []

    def observe(self, raw) -> dict:
        self.observed.append(dict(raw))
        return {"effect": "OBSERVATION_ONLY", "pause_requested": False,
                "activity_started": False, "owners_written": 0}

    def begin_from_decision(self, invitation, *, now=None) -> dict:
        self.begin_calls.append(str(invitation.get("invitation_id")))
        return dict(self.begin_result)

    def end(self, end_event, *, now=None) -> dict:
        self.end_calls.append(str(end_event.get("invitation_id")))
        return dict(self.end_result)

    def read_session(self) -> dict:
        return dict(self.session)

    def contact_context(self, invitation=None) -> dict:
        return {"available": True, "co_presence_state": self.session.get("state", "NONE"),
                "invitation_scope": (invitation or {}).get("scope")}


def _invitation_block(invitation_id: str = "inv:b:1") -> dict:
    return {"event_kind": "CO_PRESENCE_INVITATION", "invitation_id": invitation_id,
            "source_ref": "channel:local-inbox", "occurred_at": "2026-10-20T06:00:00+00:00",
            "scope": SCOPE, "requires_engagement": True}


def _end_block(invitation_id: str = "inv:b:1") -> dict:
    return {"event_kind": "CO_PRESENCE_END", "invitation_id": invitation_id,
            "occurred_at": "2026-10-20T06:20:00+00:00", "explicit_end": True,
            "binding": {"bound": True, "invitation_event_ref": "contact_evt:0001"}}


class SocialContactPortDispatchTest(unittest.TestCase):
    """One event, one terminal result -- and a message can never become engagement."""

    def _port(self, protocol: FakeProtocol, ledger: FakeLedger | None = None):
        ledger = ledger or FakeLedger()
        port = social.SocialContactPort(
            runtime=None, state_root=Path.cwd(), subject_id="test:subject", home="/tmp/home",
            inbox=ledger, protocol=protocol, max_per_tick=2)
        return port, ledger, protocol

    def test_an_ordinary_message_is_observed_and_nothing_else(self):
        protocol = FakeProtocol()
        port, ledger, _ = self._port(protocol)
        ref = ledger.add("LOCAL_MESSAGE", body=BODY_MARKER)
        out = port.tick()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["llm_calls"], 0)
        self.assertEqual(out["processed"][0]["outcome"], social.OUTCOME_OBSERVED)
        self.assertFalse(out["processed"][0]["pause_requested"])
        self.assertFalse(out["processed"][0]["activity_started"])
        self.assertEqual(len(protocol.observed), 1)
        self.assertEqual(len(protocol.begin_calls), 0)
        self.assertEqual(ledger.events[ref]["processing_state"], "PROCESSED")
        self.assertEqual(ledger.recorded[-1]["outcome"], "OBSERVED")
        self.assertEqual(ledger.recorded[-1]["reason_code"], "OBSERVED_ONLY")

    def test_an_accepted_invitation_records_the_session_not_a_second_decision(self):
        protocol = FakeProtocol(begin_result={
            "ok": True, "stage": "JOINT_OPEN", "decision": "ACCEPT", "llm_calls": 1,
            "session_ref": "co_presence:inv:b:1", "joint_activity_ref": "activity:0001",
            "private_activity_ref": "activity:0002", "activity_started": True,
            "decision_record_ref": "dec:0001"})
        port, ledger, _ = self._port(protocol)
        ref = ledger.add("CO_PRESENCE_INVITATION", invitation=_invitation_block())
        out = port.tick()
        self.assertEqual(out["llm_calls"], 1, out)
        item = out["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_ACCEPTED)
        self.assertEqual(protocol.begin_calls, ["inv:b:1"])
        self.assertEqual(ledger.recorded[-1]["session_ref"], "co_presence:inv:b:1")
        self.assertEqual(ledger.recorded[-1]["decision_ref"], "dec:0001")
        self.assertEqual(ledger.events[ref]["processing_state"], "PROCESSED")

    def test_a_deferral_names_its_condition_and_is_never_re_asked(self):
        protocol = FakeProtocol(begin_result={
            "ok": False, "stage": "NOT_ACCEPTED", "decision": "DEFER",
            "reason_code": "DECISION_DEFER", "llm_calls": 1,
            "defer_condition": "co_presence_invitation:inv:b:1:reconsidered"})
        port, ledger, _ = self._port(protocol)
        ref = ledger.add("CO_PRESENCE_INVITATION", invitation=_invitation_block())
        first = port.tick()
        self.assertEqual(first["processed"][0]["outcome"], social.OUTCOME_DEFERRED)
        self.assertEqual(ledger.events[ref]["processing_state"], "DEFERRED")
        self.assertEqual(ledger.recorded[-1]["defer_condition"],
                         "co_presence_invitation:inv:b:1:reconsidered")
        # the next tick does not ask the model again: the event is not pending any more
        second = port.tick()
        self.assertEqual(second["processed"], [])
        self.assertEqual(second["llm_calls"], 0)
        self.assertEqual(len(protocol.begin_calls), 1)

    def test_a_failed_decision_is_unknown_with_a_named_reason_and_not_redone(self):
        protocol = FakeProtocol(begin_result={
            "ok": False, "stage": "DECISION_CYCLE_REFUSED", "decision": "",
            "reason_code": "MODEL_TIMEOUT", "detail": "no answer", "llm_calls": 1})
        port, ledger, _ = self._port(protocol)
        ref = ledger.add("CO_PRESENCE_INVITATION", invitation=_invitation_block())
        out = port.tick()
        item = out["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_UNKNOWN)
        self.assertEqual(ledger.events[ref]["processing_state"], "UNKNOWN")
        self.assertEqual(ledger.recorded[-1]["reason_code"], "MODEL_TIMEOUT")
        self.assertEqual(port.tick()["processed"], [])
        self.assertEqual(len(protocol.begin_calls), 1)
        # the way back is the accounting read, not a blind redo
        reconciled = port.reconcile(ledger.events[ref]["operation_key"])
        self.assertFalse(reconciled["redecided"])
        self.assertIn("status", reconciled)

    def test_an_explicit_end_is_recorded_as_ended_and_completion_is_reported(self):
        protocol = FakeProtocol(end_result={
            "ok": True, "already_closed": False,
            "joint_completion": {"ok": True, "activity_status": "COMPLETED",
                                 "reason_code": "JOINT_ACTIVITY_COMPLETED",
                                 "completed_transition": True},
            "new_eligibility_refs": [{"eligibility_id": "elig:0001", "status": "OPEN"}],
            "private_activity": {"status": "WAITING"}})
        port, ledger, _ = self._port(protocol)
        ref = ledger.add("CO_PRESENCE_END", end=_end_block())
        out = port.tick()
        item = out["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_ENDED)
        self.assertTrue(item["joint_completed"])
        self.assertEqual(item["new_eligibility_refs"][0]["eligibility_id"], "elig:0001")
        self.assertEqual(ledger.recorded[-1]["invitation_ref"], "contact_evt:0001")
        self.assertEqual(ledger.recorded[-1]["reason_code"], "CO_PRESENCE_ENDED")
        self.assertEqual(ledger.events[ref]["processing_state"], "PROCESSED")

    def test_an_end_that_could_not_reach_a_terminal_joint_state_says_so(self):
        protocol = FakeProtocol(end_result={
            "ok": False, "joint_completion": {"ok": False, "activity_status": "ACTIVE",
                                              "reason_code": "JOINT_ACTIVITY_NOT_TERMINAL"},
            "new_eligibility_refs": [], "private_activity": {"status": "WAITING"}})
        port, ledger, _ = self._port(protocol)
        ledger.add("CO_PRESENCE_END", end=_end_block())
        item = port.tick()["processed"][0]
        self.assertFalse(item["joint_completed"])
        self.assertEqual(ledger.recorded[-1]["reason_code"],
                         "CO_PRESENCE_END_JOINT_NOT_TERMINAL")

    def test_views_never_carry_a_message_body(self):
        protocol = FakeProtocol()
        port, ledger, _ = self._port(protocol)
        ledger.add("LOCAL_MESSAGE", body=BODY_MARKER)
        port.tick()
        view = port.view()
        block = port.cognition_block()
        self.assertNotIn(BODY_MARKER, json.dumps(view, ensure_ascii=False))
        self.assertNotIn(BODY_MARKER, json.dumps(block, ensure_ascii=False))
        self.assertFalse(view["private_text_included"])
        self.assertFalse(block["private_text_included"])
        self.assertFalse(block["delivered_to_user"])
        self.assertEqual(port.describe()["owns_activity"], False)
        self.assertEqual(port.describe()["outbound_delivery"], False)

    def test_one_tick_is_bounded_and_reports_what_is_left(self):
        protocol = FakeProtocol()
        port, ledger, _ = self._port(protocol)
        for _ in range(5):
            ledger.add("LOCAL_MESSAGE", body="hello")
        out = port.tick()
        self.assertEqual(len(out["processed"]), 2)
        self.assertEqual(out["max_per_tick"], 2)
        self.assertEqual(out["still_pending"], 3)

    def test_a_port_with_no_seam_says_which_half_is_missing(self):
        port = social.SocialContactPort(runtime=None, state_root=Path.cwd(), subject_id="s",
                                       home="/tmp/home", inbox=None, protocol=None)
        out = port.tick()
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason_code"], social.REASON_PORT_UNAVAILABLE)
        self.assertEqual(out["missing"], ["inbox", "protocol"])


# =====================================================================================
# 2. 整链：真 assembly / 真 runner_tick / 真 evaluate_once（只把模型换成原始 JSON）
# =====================================================================================


def _decision_response(cognition, raw: dict):
    import agency_decision_ag1 as ag1

    cognition.call_count += 1
    return ag1.CognitiveDecisionResponse(
        model_run_ref="model_run:stub:b:%d" % cognition.call_count,
        model_id=cognition.model_id, model_version=cognition.model_version,
        raw_output=raw, latency_ms=1)


@posix_only
class SocialLifeChainTest(DScenarioBase):
    """阅读→来话观察→邀请→交接 park→joint→end→可控窗口内新 Decision 续读第二页。"""

    # -- fixture -------------------------------------------------------------------------

    def _social_fixture(self, cognition=None):
        """真 runtime + 真 assembly + A 的真账本；父级可直接复用这一个夹具。"""
        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = cognition or self._no_action_cognition()
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.memory, fixture.env, fixture.cognition = prepared.memory, env, cognition
        contacts = fixture.assembled["contacts"]
        self.assertEqual(contacts["status"], "READY", contacts)
        fixture.inbox = lc.LocalContactInbox(
            odl.life_state_root(self.instance), subject_id=prepared.data["subject_id"],
            home_ref=contacts["home_ref"])
        fixture.protocol = fixture.assembled["cycle"].co_presence
        self.assertIsNotNone(fixture.protocol)
        fixture.contacts_assembled = contacts
        return fixture

    def _submit(self, fixture, payload: dict) -> str:
        out = fixture.inbox.submit(payload)
        self.assertTrue(out["ok"], out)
        return str(out["event_ref"])

    def _detail(self, fixture, event_ref: str) -> dict:
        got = fixture.inbox.get_event(event_ref, include_body=True)
        self.assertTrue(got["ok"], got)
        return dict(got["data"]["event"])

    def _tick(self, fixture, cognition=None) -> dict:
        if cognition is not None:
            fixture.runtime.decision_service.cognition_adapter = cognition
        return assembly.runner_tick(fixture.assembled, act=True)

    def _open_window(self, fixture):
        """Open the resident's check window with a controlled clock -- no sleep, no luck."""
        runner = fixture.assembled["runner"]
        base = datetime.now(timezone.utc)
        runner._clock = lambda: base + timedelta(minutes=int(runner.interval_minutes) + 1)
        return runner

    def _reading(self, fixture):
        runtime, cycle, data = fixture.runtime, fixture.cycle, fixture.data
        runtime.decision_service.cognition_adapter = self._reading_cognition(data)
        read = cycle.evaluate_once(trigger_ref="evt:b:read", act=True)
        self.assertEqual(read["outcome"], "ACTED", read)
        self.assertEqual(read["reading"]["page"], 1, read["reading"])
        ref = str(read["activity_ref"])
        return ref, self.activity(runtime, ref)

    def _state(self, fixture, activity_ref: str) -> dict:
        row = self.activity(fixture.runtime, activity_ref)
        return {"status": row.get("status"), "revision": row.get("revision"),
                "checkpoint_ref": row.get("checkpoint_ref"),
                "resume_condition_ref": row.get("resume_condition_ref"),
                "foreground": fixture.runtime.canonical_state().get("foreground_activity_ref")}

    def _reading_page(self, fixture) -> int:
        import subject_private_materials as materials
        situation = materials.read_reading_situation(
            fixture.port, reading_artifact_id=fixture.data["reading_artifact_id"],
            private_items_artifact_id=fixture.data["private_items_artifact_id"])
        return int(situation["position"]["page"] or 0)

    def _joint_activities(self, fixture) -> list[dict]:
        return [dict(row) for row in fixture.runtime.activity_read.list_open_activities()
                if str(row.get("activity_kind") or "") == "FINITE_CO_PRESENCE"]

    # -- raw-JSON model stubs --------------------------------------------------------------

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
                        "short_rationale": "有人来，我先看看",
                        "deferred_candidate_ids": []})
                return _decision_response(inner, {
                    "decision": "NO_ACTION", "selected_candidate_id": None,
                    "reason_codes": allowed[:1],
                    "short_rationale": "没有可选的来访候选", "deferred_candidate_ids": []})

        return CoPresenceCognition()

    def _broken_cognition(self):
        class BrokenCognition(StubCognition):
            def invoke(inner, request, *, schema_retry_index=0):
                raise RuntimeError("model transport down")

        return BrokenCognition("artifact")

    # -- 1. the whole chain ----------------------------------------------------------------

    def test_read_observe_invite_joint_end_then_a_new_decision_reads_page_two(self):
        fixture = self._social_fixture()
        runtime, cycle, data = fixture.runtime, fixture.cycle, fixture.data
        reading_ref, before = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        self.assertEqual(baseline["status"], "ACTIVE", baseline)
        self.assertTrue(baseline["checkpoint_ref"])
        self.assertEqual(baseline["foreground"], reading_ref)

        # (a) an ordinary message: observed, and the reading is *not touched at all*
        message_ref = self._submit(fixture, {
            "event_kind": "LOCAL_MESSAGE", "text": "在吗？我在门口。",
            "operation_key": "op:b:msg:1"})
        step = self._tick(fixture, self._no_action_cognition())["contacts"]
        self.assertEqual(step["llm_calls"], 0)
        self.assertEqual(step["processed"][0]["outcome"], social.OUTCOME_OBSERVED)
        self.assertFalse(step["processed"][0]["pause_requested"])
        self.assertFalse(step["processed"][0]["activity_started"])
        self.assertEqual(self._detail(fixture, message_ref)["processing_state"], "PROCESSED")
        self.assertEqual(self._state(fixture, reading_ref), baseline)
        self.assertEqual(fixture.protocol.read_session()["state"], "NONE")

        # (b) the explicit invitation: one decision, then the *adoption* hands the foreground over
        invitation_ref = self._submit(fixture, {
            "event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
            "text": "要不要一起坐一会儿？", "operation_key": "op:b:inv:1"})
        invitation = self._detail(fixture, invitation_ref)["invitation"]
        invitation_cognition = self._co_presence_cognition("START")
        step = self._tick(fixture, invitation_cognition)["contacts"]
        item = step["processed"][0]
        self.assertEqual(step["llm_calls"], 1, step)
        self.assertEqual(item["outcome"], social.OUTCOME_ACCEPTED, (item, invitation_cognition.seen))
        self.assertTrue(item["joint_activity_ref"], item)
        self.assertTrue(item["park_applied"], item)
        session = fixture.protocol.read_session()
        self.assertEqual(session["state"], "OPEN", session)
        self.assertEqual(session["invitation_id"], invitation["invitation_id"])
        private = self.activity(runtime, reading_ref)
        self.assertEqual(private["status"], "WAITING", private)
        self.assertEqual(private["checkpoint_ref"], baseline["checkpoint_ref"], private)
        self.assertTrue(private.get("resume_condition_ref"), private)
        joint = self.activity(runtime, str(item["joint_activity_ref"]))
        self.assertEqual(joint["status"], "ACTIVE", joint)
        self.assertEqual(runtime.canonical_state().get("foreground_activity_ref"),
                         joint["activity_id"])

        # (c) the explicit end, in a *due* resident window: the owner completes the joint
        #     Activity, opens the private work's resume eligibility, and the same normal window
        #     sees it -- a fresh Decision resumes the original Activity from its own checkpoint.
        self._open_window(fixture)
        resume_cognition = self._resume_cognition()
        end_ref = self._submit(fixture, {
            "event_kind": "CO_PRESENCE_END", "invitation_id": invitation["invitation_id"],
            "explicit_end": True, "operation_key": "op:b:end:1"})
        tick = self._tick(fixture, resume_cognition)
        step = tick["contacts"]
        item = step["processed"][0]
        self.assertEqual(step["llm_calls"], 0, step)
        self.assertEqual(item["outcome"], social.OUTCOME_ENDED, item)
        self.assertTrue(item["joint_completed"], item)
        self.assertEqual(self._detail(fixture, end_ref)["processing_state"], "PROCESSED")
        self.assertEqual(self.activity(runtime, joint["activity_id"])["status"], "COMPLETED")
        self.assertEqual((item.get("private_activity") or {}).get("status"), "WAITING", item)
        self.assertEqual(fixture.protocol.read_session()["state"], "CLOSED")
        due = tick.get("due") or {}

    def test_a_new_window_resumes_the_parked_reading_and_reads_page_two(self):
        """The end opens eligibility; a fresh normal event-window decision resumes and reads."""
        fixture = self._social_fixture()
        runtime = fixture.runtime
        reading_ref, _ = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        ref = self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                                     "text": "一起吗", "operation_key": "op:b:resume:inv"})
        invitation = self._detail(fixture, ref)["invitation"]
        item = self._tick(fixture, self._co_presence_cognition("START"))["contacts"]["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_ACCEPTED)
        joint_ref = item["joint_activity_ref"]
        resume_cognition = self._resume_cognition()
        original_invoke = resume_cognition.invoke
        before_resume = []

        def invoke(request, *, schema_retry_index=0):
            # Read owners at the exact boundary: end itself has not resumed anything.
            self.assertEqual(self.activity(runtime, joint_ref)["status"], "COMPLETED")
            self.assertEqual(self.activity(runtime, reading_ref)["status"], "WAITING")
            rights = [row for row in self.eligibilities(runtime)
                      if row.get("activity_id") == reading_ref and row.get("status") == "OPEN"]
            self.assertEqual(len(rights), 1, rights)
            self.assertEqual(self._reading_page(fixture), 1)
            before_resume.append(True)
            return original_invoke(request, schema_retry_index=schema_retry_index)

        resume_cognition.invoke = invoke
        end_ref = self._submit(fixture, {"event_kind": "CO_PRESENCE_END",
                                         "invitation_id": invitation["invitation_id"],
                                         "explicit_end": True,
                                         "operation_key": "op:b:resume:end"})
        tick = self._tick(fixture, resume_cognition)
        ended = tick["contacts"]["processed"][0]
        self.assertEqual(ended["outcome"], social.OUTCOME_ENDED, ended)
        self.assertTrue(ended["new_eligibility_refs"], ended)
        self.assertEqual(self._detail(fixture, end_ref)["processing_state"], "PROCESSED")
        self.assertTrue(before_resume)
        self.assertTrue(resume_cognition.resume_offered)
        wakes = [*tick["events"], tick["due"]]
        self.assertTrue(any(row.get("decision") == "RESUME" and row.get("status") == "ACTED"
                            for row in wakes), wakes)
        self.assertEqual(self.activity(runtime, reading_ref)["status"], "ACTIVE")
        self.assertEqual(self.activity(runtime, reading_ref)["checkpoint_ref"], baseline["checkpoint_ref"])
        self.assertEqual(self._reading_page(fixture), 2)

    # -- 2. refusing costs nothing ---------------------------------------------------------

    def test_a_refused_invitation_never_touches_the_running_activity(self):
        fixture = self._social_fixture()
        runtime, cycle = fixture.runtime, fixture.cycle
        reading_ref, _ = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        state_root = odl.life_state_root(self.instance)
        session_path = state_root / "co_presence.json"

        def assert_untouched(label: str) -> None:
            self.assertEqual(self._state(fixture, reading_ref), baseline, label)
            self.assertEqual(fixture.protocol.read_session()["state"], "NONE", label)
            self.assertFalse(session_path.exists(), label)
            self.assertEqual(self._joint_activities(fixture), [], label)
            self.assertEqual(runtime.canonical_state().get("foreground_activity_ref"),
                             reading_ref, label)

        # (a) NO_ACTION
        ref = self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                                     "text": "a", "operation_key": "op:b:no"})
        item = self._tick(fixture, self._no_action_cognition())["contacts"]["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_DECLINED, item)
        assert_untouched("NO_ACTION")
        self.assertEqual((self._detail(fixture, ref).get("result") or {}).get("outcome"),
                         "DECLINED")

        # (b) DEFER -- named, and the next tick does not ask again
        ref = self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                                     "text": "b", "operation_key": "op:b.defer"})
        item = self._tick(fixture, self._co_presence_cognition("DEFER"))["contacts"]["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_DEFERRED, item)
        self.assertTrue(item["defer_condition"], item)
        assert_untouched("DEFER")
        quiet = self._tick(fixture, self._co_presence_cognition("START"))
        self.assertEqual(quiet["contacts"]["processed"], [])
        self.assertEqual(quiet["contacts"]["llm_calls"], 0)

        # (c) the model itself fails
        self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                               "text": "c", "operation_key": "op:b:broken"})
        item = self._tick(fixture, self._broken_cognition())["contacts"]["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_UNKNOWN, item)
        assert_untouched("BROKEN_MODEL")

        class InvalidSchemaCognition(StubCognition):
            def invoke(inner, request, *, schema_retry_index=0):
                return _decision_response(inner, {"unexpected": "not a decision"})

        invalid_ref = self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                                            "text": "invalid", "operation_key": "op:b:invalid-schema"})
        invalid = self._tick(fixture, InvalidSchemaCognition("artifact"))["contacts"]["processed"][0]
        self.assertEqual(invalid["outcome"], social.OUTCOME_UNKNOWN, invalid)
        self.assertEqual(invalid["reason_code"], "DECISION_NOT_COMMITTED", invalid)
        self.assertEqual(self._detail(fixture, invalid_ref)["processing_state"], "UNKNOWN")
        assert_untouched("INVALID_SCHEMA")

        # (d) reopening the same instance does not accept anything again
        reopened = lc.LocalContactInbox(
            state_root, subject_id=fixture.data["subject_id"],
            home_ref=fixture.contacts_assembled["home_ref"])
        self.assertEqual(reopened.pending()["data"]["events"], [])
        step = self._tick(fixture, self._co_presence_cognition("START"))["contacts"]
        self.assertEqual(step["processed"], [])
        assert_untouched("REOPEN")

    # -- 3. the instance's own request ledger ----------------------------------------------

    def test_the_ledger_is_charged_once_and_a_spent_ledger_costs_no_decision(self):
        fixture = self._social_fixture()
        runtime, cycle = fixture.runtime, fixture.cycle
        reading_ref, _ = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        ledger = cycle.budget.requests
        self.assertIsNotNone(ledger, "the instance must carry a request ledger")
        before = dict(ledger.view())
        self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                               "text": "一", "operation_key": "op:b:budget:1"})
        import life_request_budget as lrb
        production_wrapped = lrb.BudgetedCognitionAdapter(self._co_presence_cognition("START"), ledger)
        step = self._tick(fixture, production_wrapped)["contacts"]
        self.assertEqual(step["llm_calls"], 1, step)
        after = dict(ledger.view())
        self.assertEqual(int(after["utc_day_calls"]) - int(before["utc_day_calls"]), 1,
                         (before, after))
        self.assertEqual(self.activity(runtime, reading_ref)["status"], "WAITING")
        # close the joint time again so the next case starts from a quiet foreground
        self._submit(fixture, {"event_kind": "CO_PRESENCE_END",
                               "invitation_id": fixture.protocol.read_session()["invitation_id"],
                               "explicit_end": True, "operation_key": "op:b:budget:end"})
        self._tick(fixture, self._no_action_cognition())

        # spend the ledger through its own API, then hand it another invitation: no request,
        # and no change to anything at all
        import life_request_budget as lrb

        for _ in range(int(after.get("utc_day_max") or 96) + 1):
            try:
                ledger.reserve("decision")
            except lrb.RequestBudgetError:
                break
        self.assertNotEqual(ledger.check().get("allowed"), True, ledger.view())
        spent_before = self._state(fixture, reading_ref)
        refused = self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                                        "text": "二", "operation_key": "op:b:budget:2"})
        item = self._tick(fixture, self._co_presence_cognition("START"))["contacts"]["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_DEFERRED, item)
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(self._state(fixture, reading_ref), spent_before)
        self.assertEqual((self._detail(fixture, refused).get("result") or {}).get("outcome"),
                         "DEFERRED")

    # -- 4. ATOMIC is never forced ---------------------------------------------------------

    def test_an_atomic_foreground_is_declined_and_never_handed_over(self):
        fixture = self._social_fixture()
        runtime, cycle = fixture.runtime, fixture.cycle
        reading_ref, _ = self._reading(fixture)
        runtime.interruption_coordinator.configure_activity_attention(
            activity_id=reading_ref, attention_occupancy="ATOMIC",
            interruptibility="ATOMIC", atomic_region_ref="atomic:b:1",
            max_atomic_duration_seconds=120)
        atomic_state = self._state(fixture, reading_ref)
        self._submit(fixture, {"event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE,
                               "text": "打断一下", "operation_key": "op:b:atomic"})
        step = self._tick(fixture, self._co_presence_cognition("START"))["contacts"]
        item = step["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_DECLINED, item)
        self.assertIn("ATOMIC", str(item["reason_code"]), item)
        self.assertEqual(step["llm_calls"], 0, step)
        self.assertEqual(self._state(fixture, reading_ref), atomic_state)
        self.assertEqual(fixture.protocol.read_session()["state"], "NONE")
        self.assertEqual(self._joint_activities(fixture), [])


#: ``DScenarioBase`` 继承 ``CycleTest``，而它有自己的一整天用例；那些用例在这里会以本模块的
#: 次序再跑一遍，既不是本阶段要验的，也会因为共用同一进程而互相干扰。这里只保留本类自己的用例。
_OWN_TESTS = (
    "test_read_observe_invite_joint_end_then_a_new_decision_reads_page_two",
    "test_a_refused_invitation_never_touches_the_running_activity",
    "test_the_ledger_is_charged_once_and_a_spent_ledger_costs_no_decision",
    "test_an_atomic_foreground_is_declined_and_never_handed_over",
    "test_a_new_window_resumes_the_parked_reading_and_reads_page_two",
)
if CHAIN_IMPORT_ERROR is None:
    # ``delattr`` cannot remove an inherited name (the subclass has no such attribute of its own),
    # so the inherited cases are shadowed with a non-callable value -- unittest only collects
    # attributes it can call.  The base class is then dropped from this module's namespace for the
    # same reason: importing it as a plain name made its own cases collectable here.
    for _inherited in [name for name in dir(SocialLifeChainTest) if name.startswith("test_")
                       and name not in _OWN_TESTS]:
        setattr(SocialLifeChainTest, _inherited, None)
    del DScenarioBase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
