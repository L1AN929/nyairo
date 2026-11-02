#!/usr/bin/env python3
"""原子步骤期间的普通聊天：本地回复真的存下来、按 id 取得到，前台不动。

这份测试钉的是可以直接复跑的窄语义（真链证据在 ``scripts/atomic_chat_boundary_demo.py``）：

1. ``FiniteCoPresence.observe`` 对一条普通消息读出 **LR-3 自己的** 沟通可用性：
   ATOMIC 前台是 ``BOUNDARY_DEFERRED``（可以回，但前台到边界前不动），空闲是 ``OPEN``，
   读不到就说读不到而不是猜 ``OPEN``。它不写任何 owner，也不把消息读成暂停/开始活动。
2. 回复真的归档在消息 owner 上：只有持有认领的消费者能写；一条消息只有一句；同样的重复
   投递重放而不是生成第二句；取正文必须显式 ``include_text``。
3. 独立消息 lane 只消费普通消息：邀请/结束留在串行步骤里；lane 起得来、停得干净。
4. responder 的拒绝（预算/空答案/过长）是具名失败，不会被记成"她回过"。

边界：不跑真实模型、不读凭据、不开旧实例、不发外部消息；“回应”不是用户已收到。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_co_presence as cop  # noqa: E402
import life_local_contact as lc  # noqa: E402
import life_social_contacts as lsc  # noqa: E402
import local_message_lane as lml  # noqa: E402
import local_message_responder as lmr  # noqa: E402

SUBJECT = "test:atomic-chat"
HOME = "home:0123456789ab"
WHEN = "2026-11-20T09:00:00+00:00"
#: 夹具根只建不删（与仓库其他夹具一致，便于复查）。
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairo-atomic-chat-test-"))


def _root(name: str) -> Path:
    path = FIXTURE_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _digest_tree(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_file():
            out[str(item.relative_to(path))] = hashlib.sha256(item.read_bytes()).hexdigest()
    return out


class _FakeCoordinator:
    def __init__(self, view: Mapping[str, Any]) -> None:
        self._view = dict(view)

    def get_current_life_view(self) -> dict[str, Any]:
        return dict(self._view)


class _FakeRuntime:
    """Only the one read the projection uses; nothing here is a domain owner."""

    def __init__(self, view: Mapping[str, Any]) -> None:
        self.interruption_coordinator = _FakeCoordinator(view)


def _protocol(name: str, view: Mapping[str, Any]) -> cop.FiniteCoPresence:
    return cop.FiniteCoPresence(_FakeRuntime(view), state_root=_root(name),
                                subject_id=SUBJECT, home=HOME)


class ObserveAvailabilityTest(unittest.TestCase):
    def test_atomic_foreground_defers_the_boundary_not_the_reply(self) -> None:
        protocol = _protocol("atomic", {
            "foreground_activity_ref": "actv_atomic",
            "interruptibility": "ATOMIC",
            "communication_availability": "BOUNDARY_DEFERRED",
        })
        seen = protocol.observe({"kind": "LOCAL_MESSAGE", "body": "在吗"})
        self.assertEqual(seen["effect"], cop.EFFECT_OBSERVATION_ONLY)
        self.assertEqual(seen["communication_availability"], "BOUNDARY_DEFERRED")
        self.assertTrue(seen["respondable"])
        self.assertTrue(seen["boundary_deferred"])
        self.assertTrue(seen["foreground_atomic"])
        self.assertFalse(seen["pause_requested"])
        self.assertFalse(seen["activity_started"])
        self.assertEqual(seen["owners_written"], 0)

    def test_idle_is_open_and_respondable(self) -> None:
        protocol = _protocol("idle", {
            "life_state": "IDLE",
            "communication_availability": "OPEN",
            "interruptibility": "IMMEDIATE",
        })
        seen = protocol.observe({"kind": "LOCAL_MESSAGE", "body": "在吗"})
        self.assertEqual(seen["communication_availability"], "OPEN")
        self.assertTrue(seen["respondable"])
        self.assertFalse(seen["boundary_deferred"])
        self.assertFalse(seen["foreground_atomic"])

    def test_unreadable_life_view_is_named_not_guessed_open(self) -> None:
        protocol = _protocol("unreadable", {"read_error": "OSError: torn state"})
        seen = protocol.observe({"kind": "LOCAL_MESSAGE", "body": "在吗"})
        self.assertIsNone(seen["communication_availability"])
        self.assertFalse(seen["respondable"])
        self.assertIn("torn state", str(seen["life_view_error"]))
        self.assertFalse(seen["pause_requested"])
        self.assertFalse(seen["activity_started"])

    def test_an_invitation_is_still_not_an_ordinary_message(self) -> None:
        protocol = _protocol("invitation", {
            "life_state": "IDLE", "communication_availability": "OPEN"})
        seen = protocol.observe({"kind": cop.KIND_INVITATION})
        self.assertEqual(seen["effect"], cop.EFFECT_INVITATION)
        self.assertEqual(seen["kind"], cop.KIND_INVITATION)

    def test_observation_writes_nothing(self) -> None:
        root = _root("pure")
        protocol = cop.FiniteCoPresence(_FakeRuntime({
            "foreground_activity_ref": "actv_atomic",
            "interruptibility": "ATOMIC",
            "communication_availability": "BOUNDARY_DEFERRED",
        }), state_root=root, subject_id=SUBJECT, home=HOME)
        before = _digest_tree(root)
        protocol.observe({"kind": "LOCAL_MESSAGE", "body": "在吗"})
        self.assertEqual(_digest_tree(root), before)


class _Responder:
    """一个最朴素的 responder：固定一句话，可以抛错，也可以返回具名拒绝。"""

    def __init__(self, *, text: str = "嗯，我在", raises: bool = False,
                 refusal: str = "") -> None:
        self.text = text
        self.raises = raises
        self.refusal = refusal
        self.calls = 0

    def respond(self, *, event_kind: str, observed: Mapping[str, Any],
                text: str = "") -> Mapping[str, Any]:
        self.calls += 1
        if self.raises:
            raise RuntimeError("responder exploded")
        if self.refusal:
            return {"ok": False, "reason_code": self.refusal, "detail": "refused"}
        return {"ok": True, "text": self.text}


def _inbox(name: str) -> lc.LocalContactInbox:
    return lc.LocalContactInbox(_root(name), subject_id=SUBJECT, home_ref=HOME)


def _claimed(inbox: lc.LocalContactInbox, *, kind: str = lc.KIND_MESSAGE,
             tag: str = "one",
             body: str = "在吗") -> tuple[str, str]:
    payload: dict[str, Any] = {"kind": kind, "body": body,
                               "occurred_at": WHEN, "operation_key": "op:%s" % tag}
    if kind == lc.KIND_INVITATION:
        payload = {"kind": kind, "scope": "一起读一页", "occurred_at": WHEN,
                   "operation_key": "op:%s" % tag}
    submitted = dict(inbox.submit(payload))
    assert submitted.get("ok"), submitted
    event_ref = str(submitted.get("event_ref") or "")
    claim = dict(inbox.claim(event_ref, consumer="test_consumer"))
    assert claim.get("ok"), claim
    return event_ref, str(claim.get("claim_token") or "")


def _port(name: str, *, inbox: Any = None, responder: Any = None,
          note: Mapping[str, Any] = None,
          protocol: Any = None) -> lsc.SocialContactPort:
    return lsc.SocialContactPort(
        _FakeRuntime({"life_state": "IDLE", "communication_availability": "OPEN"}),
        state_root=_root(name), subject_id=SUBJECT, home=HOME, inbox=inbox,
        protocol=protocol, responder=responder, responder_note=note)


class LocalReplyArchiveTest(unittest.TestCase):
    def test_no_responder_says_so_instead_of_pretending(self) -> None:
        reply = _port("no-responder")._local_reply({"respondable": True})
        self.assertFalse(reply["responded"])
        self.assertFalse(reply["archived"])
        self.assertEqual(reply["reason_code"], "LOCAL_REPLY_NOT_WIRED")

    def test_the_wiring_gap_is_named_from_the_assembly(self) -> None:
        reply = _port("not-configured", note={
            "reason_code": "LOCAL_REPLY_MODEL_NOT_CONFIGURED"})._local_reply(
                {"respondable": True})
        self.assertFalse(reply["responded"])
        self.assertEqual(reply["reason_code"], "LOCAL_REPLY_MODEL_NOT_CONFIGURED")

    def test_the_reply_is_archived_and_the_words_need_an_explicit_read(self) -> None:
        inbox = _inbox("archived")
        event_ref, token = _claimed(inbox)
        responder = _Responder(text="嗯，我在，等我做完这一步。")
        reply = _port("archived-port", inbox=inbox,
                      responder=responder)._local_reply(
                          {"respondable": True}, event_ref=event_ref, claim_token=token)
        self.assertTrue(reply["responded"])
        self.assertTrue(reply["archived"])
        self.assertGreater(int(reply["chars"]), 0)
        self.assertNotIn("text", reply)
        self.assertEqual(responder.calls, 1)
        plain = dict((inbox.reply(event_ref) or {}).get("data", {}).get("reply", {}))
        self.assertTrue(plain["reply_present"])
        self.assertGreater(int(plain["reply_chars"]), 0)
        self.assertNotIn("text", plain)
        with_text = dict((inbox.reply(event_ref, include_text=True) or {})
                         .get("data", {}).get("reply", {}))
        self.assertEqual(with_text["text"], "嗯，我在，等我做完这一步。")
        self.assertTrue(with_text["text_included"])
        self.assertFalse(with_text["delivered_to_user"])

    def test_the_same_reply_replays_and_a_different_one_is_refused(self) -> None:
        inbox = _inbox("one-reply")
        event_ref, token = _claimed(inbox)
        first = dict(inbox.record_reply(event_ref, claim_token=token, text="第一句"))
        self.assertTrue(first.get("ok"))
        again = dict(inbox.record_reply(event_ref, claim_token=token, text="第一句"))
        self.assertTrue(again.get("ok"))
        self.assertTrue(again.get("replayed"))
        other = dict(inbox.record_reply(event_ref, claim_token=token, text="第二句"))
        self.assertFalse(other.get("ok"))
        self.assertEqual(other.get("reason_code"), "CONTACT_REPLY_ALREADY_RECORDED")
        view = dict((inbox.reply(event_ref, include_text=True) or {})["data"]["reply"])
        self.assertEqual(view["text"], "第一句")

    def test_a_reply_needs_the_live_claim_and_an_ordinary_message(self) -> None:
        inbox = _inbox("claim-rules")
        event_ref, _token = _claimed(inbox)
        wrong = dict(inbox.record_reply(event_ref, claim_token="contact_claim:deadbeef",
                                        text="不该存"))
        self.assertFalse(wrong.get("ok"))
        self.assertEqual(wrong.get("reason_code"), "CONTACT_CLAIM_MISMATCH")
        unclaimed_inbox = _inbox("unclaimed")
        pending = dict(unclaimed_inbox.submit({"kind": lc.KIND_MESSAGE, "body": "在吗",
                                               "occurred_at": WHEN,
                                               "operation_key": "op:pending"}))
        not_claimed = dict(unclaimed_inbox.record_reply(
            str(pending.get("event_ref") or ""), claim_token="contact_claim:x", text="不该存"))
        self.assertEqual(not_claimed.get("reason_code"), "CONTACT_EVENT_NOT_CLAIMED")
        invitation_inbox = _inbox("invitation-reply")
        invitation_ref, invitation_token = _claimed(invitation_inbox,
                                                   kind=lc.KIND_INVITATION,
                                                   tag="invite")
        refused = dict(invitation_inbox.record_reply(invitation_ref,
                                                     claim_token=invitation_token,
                                                     text="不该存"))
        self.assertEqual(refused.get("reason_code"), "CONTACT_REPLY_NOT_FOR_KIND")

    def test_a_failed_or_refused_responder_keeps_nothing_but_the_message(self) -> None:
        inbox = _inbox("failure")
        event_ref, token = _claimed(inbox)
        failed = _port("failure-port", inbox=inbox,
                       responder=_Responder(raises=True))._local_reply(
                           {"respondable": True}, event_ref=event_ref, claim_token=token)
        self.assertFalse(failed["responded"])
        self.assertEqual(failed["reason_code"], "LOCAL_REPLY_FAILED")
        self.assertIn("RuntimeError", str(failed["detail"]))
        refused = dict(inbox.record_result(event_ref, outcome="UNKNOWN",
                                           claim_token=token,
                                           reason_code="RESPONDER_EXPLODED"))
        self.assertTrue(refused.get("ok"))
        after = dict(inbox.reply(event_ref))
        self.assertEqual(after.get("status"), "absent")
        self.assertFalse(dict(after["data"]["reply"])["reply_present"])

    def test_a_budget_refusal_from_the_responder_is_named(self) -> None:
        inbox = _inbox("budget")
        event_ref, token = _claimed(inbox)
        reply = _port("budget-port", inbox=inbox,
                      responder=_Responder(refusal="LOCAL_REPLY_BUDGET_REFUSED")
                      )._local_reply({"respondable": True}, event_ref=event_ref,
                                     claim_token=token)
        self.assertFalse(reply["responded"])
        self.assertEqual(reply["reason_code"], "LOCAL_REPLY_BUDGET_REFUSED")
        self.assertFalse(dict(inbox.reply(event_ref)["data"]["reply"])["reply_present"])

    def test_a_not_respondable_message_never_calls_the_responder(self) -> None:
        responder = _Responder()
        reply = _port("not-respondable", responder=responder)._local_reply(
            {"respondable": False})
        self.assertFalse(reply["responded"])
        self.assertEqual(responder.calls, 0)

    def test_an_unreadable_view_names_the_view_not_the_responder(self) -> None:
        responder = _Responder()
        reply = _port("view-error", responder=responder)._local_reply(
            {"respondable": False, "life_view_error": "OSError: torn"})
        self.assertFalse(reply["responded"])
        self.assertEqual(reply["reason_code"], "LOCAL_REPLY_LIFE_VIEW_UNREADABLE")
        self.assertEqual(responder.calls, 0)


class ResponderUnitTest(unittest.TestCase):
    class _Client:
        def __init__(self, *, text: str = "", raises: Any = None) -> None:
            self.text = text
            self.raises = raises
            self.calls = 0

        def complete(self, **_kwargs: Any) -> Any:
            self.calls += 1
            if self.raises is not None:
                raise self.raises
            return type("Answer", (), {"text": self.text})()

    def test_one_plain_answer_is_bounded_and_whitespace_collapsed(self) -> None:
        client = self._Client(text="  在的，\n我马上回你 ")
        answer = lmr.LocalMessageResponder(client).respond(
            event_kind="LOCAL_MESSAGE", observed={}, text="在吗")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["text"], "在的， 我马上回你")
        self.assertEqual(client.calls, 1)

    def test_an_empty_answer_is_not_a_reply(self) -> None:
        answer = lmr.LocalMessageResponder(self._Client(text="   ")).respond(
            observed={}, text="在吗")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], lmr.REASON_EMPTY_ANSWER)

    def test_too_long_is_refused_not_truncated(self) -> None:
        answer = lmr.LocalMessageResponder(self._Client(text="长" * 700)).respond(
            observed={}, text="在吗")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], lmr.REASON_TOO_LONG)
        self.assertEqual(answer["text"], "")

    def test_a_budget_refusal_is_named(self) -> None:
        exc = type("RequestBudgetError", (RuntimeError,),
                   {"reason_code": "REQUEST_BUDGET_UTC_DAY_LIMIT"})("spent")
        answer = lmr.LocalMessageResponder(self._Client(raises=exc)).respond(
            observed={}, text="在吗")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], lmr.REASON_BUDGET_REFUSED)


class LaneTest(unittest.TestCase):
    def test_only_ordinary_messages_are_consumed_by_the_lane(self) -> None:
        inbox = _inbox("lane-kinds")
        protocol = _protocol("lane-kinds-protocol", {
            "life_state": "IDLE", "communication_availability": "OPEN"})
        port = _port("lane-kinds-port", inbox=inbox, protocol=protocol,
                     responder=_Responder(text="在的"))
        # 两条都还 PENDING：lane 只该认领普通消息，邀请留在串行步骤里。
        inbox.submit({"kind": lc.KIND_MESSAGE, "body": "在吗", "occurred_at": WHEN,
                      "operation_key": "op:lane-message"})
        invitation = dict(inbox.submit({"kind": lc.KIND_INVITATION, "scope": "一起读一页",
                                        "occurred_at": WHEN,
                                        "operation_key": "op:lane-invite"}))
        invitation_ref = str(invitation.get("event_ref") or "")
        answer = dict(port.tick_ordinary_messages())
        self.assertTrue(answer.get("ok"))
        self.assertEqual([row.get("kind") for row in answer.get("processed") or []],
                         [lsc.KIND_MESSAGE])
        invitation = dict((inbox.get_event(invitation_ref) or {})["data"]["event"])
        self.assertNotEqual(invitation.get("processing_state"), "PROCESSED")

    def test_the_lane_answers_once_and_stops_cleanly(self) -> None:
        inbox = _inbox("lane-live")
        protocol = _protocol("lane-live-protocol", {
            "life_state": "IDLE", "communication_availability": "OPEN"})
        responder = _Responder(text="在的，我这就来。")
        port = _port("lane-live-port", inbox=inbox, protocol=protocol, responder=responder)
        lane = lml.LocalMessageLane(port, poll_seconds=0.02)
        started = dict(lane.start())
        self.assertTrue(started.get("started"))
        submitted = dict(inbox.submit({"kind": lc.KIND_MESSAGE, "body": "在吗",
                                       "occurred_at": WHEN, "operation_key": "op:live"}))
        event_ref = str(submitted.get("event_ref") or "")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            view = dict((inbox.reply(event_ref) or {}).get("data", {}).get("reply", {}))
            if view.get("reply_present"):
                break
            time.sleep(0.02)
        self.assertTrue(view.get("reply_present"))
        self.assertEqual(responder.calls, 1)
        stopped = dict(lane.stop())
        self.assertTrue(stopped.get("stopped"))
        self.assertFalse(lane.is_running())
        # 再投同一 key：重放，不生成第二句。
        again = dict(inbox.submit({"kind": lc.KIND_MESSAGE, "body": "在吗",
                                   "occurred_at": WHEN, "operation_key": "op:live"}))
        self.assertEqual(again.get("status"), "already_applied")
        self.assertEqual(again.get("event_ref"), event_ref)
        self.assertEqual(responder.calls, 1)


class ResidentLifecycleInputsTest(unittest.TestCase):
    """正式启动路径的注入口：只换最底层 client，账本默认就是实例那一本。"""

    def test_the_reply_client_is_the_only_bottom_level_injection(self) -> None:
        import autonomous_life_resident as resident

        client = object()
        life = resident.AutonomousLifeResident(
            state_root=_root("resident-inputs"), environment={}, act=False,
            offline_scripted=True, local_reply_client=client)
        self.assertIs(life.local_reply_client, client)
        # 默认不另造预算：None 就是要用本实例自己的持久账本。
        self.assertIsNone(life.local_reply_requests)
        self.assertFalse(life.act)

    def test_no_contact_port_is_named_not_guessed(self) -> None:
        import autonomous_life_resident as resident

        life = resident.AutonomousLifeResident(
            state_root=_root("resident-no-port"), environment={}, act=False,
            offline_scripted=True)
        answer = dict(life.start_message_lane())
        self.assertFalse(answer.get("ok"))
        self.assertEqual(answer.get("reason_code"), "MESSAGE_LANE_PORT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
