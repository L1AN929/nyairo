#!/usr/bin/env python3
"""v0.4 阶段 A：本地来访端口（``life_local_contact``）—— 契约、边界、坏文件、纯读。

这一份测什么
------------
``components/life/scripts/life_local_contact.py`` 是**同实例本地 transport 收件账本**：
有界事件、每个事件的 operation key、每个事件持久化的处理结果、按 key 对账。它**不是**
关系 owner，不是第二个 Activity owner，不做外发。于是这份测试只钉这几件事：

1. **落盘与重开**：真目录、真 JSON、真原子写；新建实例重开仍读得回同样的 ref/key/结果。
2. **幂等与冲突**：同 key 同 payload 回原 event；同 key 换 payload 按名拒绝，不覆盖。
3. **身份由实例定**：payload 里的 subject/home 一律不信；不同的按名拒绝，相同的也被
   忽略并存成实例自己的值（``client_identity_ignored``）。
4. **普通消息不执行**：``LOCAL_MESSAGE`` 只能是 ``OBSERVED``/``UNKNOWN``；
   ``ACCEPTED`` 被 ``CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY`` 拒绝；消息带 ``scope`` 被拒。
5. **明确邀请的事件块**能被 ``life_co_presence.parse_invitation`` 原样接受，
   ``CO_PRESENCE_END`` 的事件块能被 ``parse_end`` 原样接受（字段逐个对齐，不猜）。
6. **DEFER 具名**、终态不复活、UNKNOWN 用同 key 对账而不是盲重做。
7. **GET 纯读**：读一整圈之后目录与文件哈希**一个字节都没变**，连 lock 都不建。
8. **坏文件具名失败**：垃圾 JSON / 半写空文件 / 换 schema / 换 subject，都有各自的
   reason_code，**绝不**当成"没有事件"，也**绝不**被写路径悄悄覆盖。

证据边界（照实写）
------------------
* 不跑真实模型、不读凭据、不开原实例、不发外部消息、不建 socket。
* 夹具目录建在 session scratch / tempfile 的一次性根下，**只建不删**（不用
  ``TemporaryDirectory`` 自动清理，也不 rmtree teardown）。
* 跨进程并发在 ``tests/test_v04_local_contact_process.py``；这里只测单进程语义。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402

#: All fixtures live under one retained root: created once, never removed.
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-local-contact-"))

SUBJECT = "chiyo"
HOME = "home:0123456789ab"

WHEN = "2026-11-20T09:00:00+00:00"
LATER = "2026-11-20T10:30:00+00:00"

#: A sentinel that must never come back out of a summary / list / state answer.
PRIVATE_BODY = "PRIVATE_LOCAL_CONTACT_SENTINEL_DO_NOT_RETURN"


def _root(name: str) -> Path:
    path = FIXTURE_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _inbox(name: str, **kwargs: Any) -> lc.LocalContactInbox:
    args = dict(subject_id=SUBJECT, home_ref=HOME)
    args.update(kwargs)
    return lc.LocalContactInbox(_root(name), **args)


def _digest_tree(path: Path) -> dict[str, str]:
    """Every file under ``path`` with its sha256 -- the only honest "nothing was written"."""

    out: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_file():
            out[str(item.relative_to(path))] = hashlib.sha256(item.read_bytes()).hexdigest()
        else:
            out[str(item.relative_to(path)) + "/"] = "<dir>"
    return out


class StorageAndReopenTest(unittest.TestCase):
    """真目录、真 JSON、重开仍读得回。"""

    def test_absent_ledger_is_named_not_an_empty_fabrication(self) -> None:
        inbox = _inbox("absent")
        summary = inbox.summary()
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["data"]["ledger_state"], "ABSENT")
        self.assertEqual(summary["data"]["event_count"], 0)
        listing = inbox.list_events()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["ledger_state"], "ABSENT")
        self.assertEqual(listing["data"]["events"], [])
        pending = inbox.pending()
        self.assertEqual(pending["data"]["ledger_state"], "ABSENT")
        missing = inbox.get_event("contact_evt:0000000000000000")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["status"], "absent")
        self.assertEqual(missing["reason_code"], "CONTACT_EVENT_NOT_FOUND")
        operation = inbox.operation("contact_op:000000000000000000000000")
        self.assertEqual(operation["status"], "absent")
        self.assertTrue(operation["retryable"])
        # Nothing was created by any of those reads.
        self.assertEqual(_digest_tree(inbox.state_root), {})

    def test_submit_persists_and_reopens_with_the_same_identity(self) -> None:
        first = _inbox("reopen")
        submitted = first.submit({"event_kind": "LOCAL_MESSAGE", "text": PRIVATE_BODY,
                                  "occurred_at": WHEN})
        self.assertTrue(submitted["ok"], submitted)
        self.assertEqual(submitted["status"], "applied")
        self.assertEqual(submitted["effect"], lc.EFFECT_OBSERVATION_ONLY)
        self.assertEqual(submitted["owners_written"], 0)
        self.assertFalse(submitted["pause_requested"])
        self.assertFalse(submitted["activity_started"])
        event_ref = submitted["event_ref"]
        self.assertTrue(event_ref.startswith("contact_evt:"))
        self.assertTrue(Path(lc.state_path(first.state_root)).exists())

        reopened = lc.LocalContactInbox(first.state_root, subject_id=SUBJECT, home_ref=HOME)
        detail = reopened.get_event(event_ref, include_body=True)
        self.assertTrue(detail["ok"], detail)
        event = detail["data"]["event"]
        self.assertEqual(event["body"], PRIVATE_BODY)
        self.assertEqual(event["event_kind"], lc.KIND_MESSAGE)
        self.assertEqual(event["processing_state"], lc.STATE_PENDING)
        self.assertEqual(event["subject_id"], SUBJECT)
        self.assertEqual(event["home_ref"], HOME)
        self.assertEqual(event["channel"], lc.CHANNEL_LOCAL_INBOX)
        self.assertEqual(event["source"], lc.SOURCE_LOCAL)
        self.assertEqual(event["operation_key"], submitted["operation_key"])
        # The operation key survives the restart too.
        self.assertEqual(reopened.operation(submitted["operation_key"])["status"], "applied")

    def test_summary_and_list_never_carry_the_body(self) -> None:
        inbox = _inbox("no-body")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": PRIVATE_BODY,
                                  "occurred_at": WHEN})
        event_ref = submitted["event_ref"]
        rollup = json.dumps(inbox.summary(), ensure_ascii=False)
        self.assertNotIn(PRIVATE_BODY, rollup)
        listing = json.dumps(inbox.list_events(), ensure_ascii=False)
        self.assertNotIn(PRIVATE_BODY, listing)
        self.assertNotIn(PRIVATE_BODY, json.dumps(inbox.pending(), ensure_ascii=False))
        self.assertNotIn(PRIVATE_BODY,
                         json.dumps(inbox.operation(submitted["operation_key"]),
                                    ensure_ascii=False))
        default_detail = inbox.get_event(event_ref)
        self.assertNotIn(PRIVATE_BODY, json.dumps(default_detail, ensure_ascii=False))
        self.assertIsNone(default_detail["data"]["event"]["body"])
        self.assertFalse(default_detail["data"]["event"]["body_included"])
        self.assertFalse(default_detail["data"]["event"]["private_text_included"])
        with_body = inbox.get_event(event_ref, include_body=True)
        self.assertTrue(with_body["data"]["event"]["private_text_included"])
        self.assertGreater(with_body["data"]["event"]["body_chars"], 0)


class IdempotencyAndOperationTest(unittest.TestCase):
    """同 key 同 payload → 原 event；同 key 换 payload → 按名拒绝。"""

    def test_same_intent_is_one_event(self) -> None:
        inbox = _inbox("idempotent")
        payload = {"event_kind": "LOCAL_MESSAGE", "text": "在吗", "occurred_at": WHEN}
        first = inbox.submit(payload)
        second = inbox.submit(dict(payload))
        self.assertEqual(first["status"], "applied")
        self.assertTrue(first["created"])
        self.assertEqual(second["status"], "already_applied")
        self.assertFalse(second["created"])
        self.assertTrue(second["replayed"])
        self.assertEqual(second["event_ref"], first["event_ref"])
        self.assertEqual(second["operation_key"], first["operation_key"])
        self.assertEqual(inbox.summary()["data"]["event_count"], 1)

    def test_same_key_with_another_payload_is_refused_not_overwritten(self) -> None:
        inbox = _inbox("conflict")
        first = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "第一句",
                              "occurred_at": WHEN})
        key = first["operation_key"]
        clash = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "另一句",
                              "occurred_at": WHEN, "operation_key": key})
        self.assertFalse(clash["ok"])
        self.assertEqual(clash["status"], "rejected")
        self.assertEqual(clash["reason_code"], "CONTACT_OPERATION_KEY_CONFLICT")
        self.assertEqual(clash["writes_performed"], 0)
        # The original arrival is untouched, and no second event was minted.
        self.assertEqual(inbox.summary()["data"]["event_count"], 1)
        self.assertEqual(inbox.get_event(first["event_ref"], include_body=True)
                         ["data"]["event"]["body"], "第一句")

    def test_explicit_operation_key_replays_across_restarts(self) -> None:
        inbox = _inbox("explicit-key")
        payload = {"event_kind": "LOCAL_MESSAGE", "text": "重放测试",
                   "occurred_at": WHEN, "operation_key": "contact_op:client-supplied-0001"}
        first = inbox.submit(payload)
        self.assertEqual(first["operation_key"], "contact_op:client-supplied-0001")
        reopened = lc.LocalContactInbox(inbox.state_root, subject_id=SUBJECT, home_ref=HOME)
        again = reopened.submit(dict(payload))
        self.assertEqual(again["status"], "already_applied")
        self.assertEqual(again["event_ref"], first["event_ref"])

    def test_unknown_outcome_reconciles_by_the_same_key(self) -> None:
        inbox = _inbox("unknown")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "对账",
                                  "occurred_at": WHEN})
        claim = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        unknown = inbox.record_result(submitted["event_ref"], outcome="UNKNOWN",
                                      claim_token=claim["claim_token"],
                                      reason_code="OWNER_ANSWER_LOST")
        self.assertEqual(unknown["status"], "applied")
        self.assertEqual(unknown["processing_state"], lc.STATE_UNKNOWN)
        self.assertTrue(unknown["retryable"])
        resolved = inbox.operation(submitted["operation_key"])
        self.assertEqual(resolved["status"], "unknown")
        self.assertTrue(resolved["retryable"])
        # The same key may be re-claimed for the reconciliation pass.
        reclaim = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        self.assertEqual(reclaim["status"], "applied")
        settled = inbox.record_result(submitted["event_ref"], outcome="OBSERVED",
                                      claim_token=reclaim["claim_token"])
        self.assertEqual(settled["processing_state"], lc.STATE_PROCESSED)
        self.assertEqual(inbox.operation(submitted["operation_key"])["data"]["outcome"],
                         "OBSERVED")


class IdentityTest(unittest.TestCase):
    """来源身份由实例构造，不信客户端。"""

    def test_foreign_subject_and_home_are_refused(self) -> None:
        inbox = _inbox("identity")
        foreign_subject = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                        "subject_id": "someone-else"})
        self.assertFalse(foreign_subject["ok"])
        self.assertEqual(foreign_subject["reason_code"], "CONTACT_SUBJECT_ID_MISMATCH")
        foreign_home = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                     "home_ref": "home:ffffffffffff"})
        self.assertFalse(foreign_home["ok"])
        self.assertEqual(foreign_home["reason_code"], "CONTACT_HOME_REF_MISMATCH")
        self.assertEqual(inbox.summary()["data"]["event_count"], 0)

    def test_matching_client_identity_is_recorded_as_ignored(self) -> None:
        inbox = _inbox("identity-match")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                  "subject_id": SUBJECT, "home_ref": HOME})
        self.assertTrue(submitted["ok"], submitted)
        event = submitted["data"]["event"]
        self.assertTrue(event["client_identity_ignored"])
        self.assertEqual(event["subject_id"], SUBJECT)
        self.assertEqual(event["home_ref"], HOME)

    def test_constructor_refuses_a_missing_identity(self) -> None:
        with self.assertRaises(lc.LocalContactError) as caught:
            lc.LocalContactInbox(_root("no-subject"), subject_id="", home_ref=HOME)
        self.assertEqual(caught.exception.reason_code, "CONTACT_SUBJECT_REQUIRED")
        with self.assertRaises(lc.LocalContactError) as caught_home:
            lc.LocalContactInbox(_root("no-home"), subject_id=SUBJECT, home_ref="   ")
        self.assertEqual(caught_home.exception.reason_code, "CONTACT_HOME_REQUIRED")

    def test_another_channel_is_refused(self) -> None:
        inbox = _inbox("channel")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                "channel": "telegram"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_CHANNEL_REFUSED")
        self.assertEqual(inbox.summary()["data"]["event_count"], 0)


class MessageVersusInvitationTest(unittest.TestCase):
    """普通来话只是观察；明确邀请才是邀请。"""

    def test_ordinary_message_can_never_be_read_as_engagement(self) -> None:
        inbox = _inbox("message-guard")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "我来啦",
                                  "occurred_at": WHEN})
        self.assertEqual(submitted["effect"], lc.EFFECT_OBSERVATION_ONLY)
        claim = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        for outcome in ("ACCEPTED", "DECLINED", "DEFERRED", "ENDED"):
            refused = inbox.record_result(submitted["event_ref"], outcome=outcome,
                                          claim_token=claim["claim_token"],
                                          defer_condition="什么时候都行")
            self.assertFalse(refused["ok"], outcome)
            self.assertEqual(refused["reason_code"],
                             "CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY")
        observed = inbox.record_result(submitted["event_ref"], outcome="OBSERVED",
                                       claim_token=claim["claim_token"])
        self.assertEqual(observed["status"], "applied")
        self.assertEqual(observed["processing_state"], lc.STATE_PROCESSED)
        self.assertIn("pause_requested", observed["data"]["event"])
        self.assertFalse(observed["data"]["event"]["pause_requested"])
        self.assertFalse(observed["data"]["event"]["activity_started"])

    def test_a_message_that_carries_an_invitation_scope_is_refused(self) -> None:
        inbox = _inbox("message-scope")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "一起坐坐?",
                                "scope": "一起坐着", "invitation_id": "localinvite:abc"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_FIELD_NOT_ALLOWED_FOR_KIND")
        self.assertEqual(inbox.summary()["data"]["event_count"], 0)

    def test_unknown_payload_fields_are_refused(self) -> None:
        inbox = _inbox("payload-whitelist")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                "activity_status": "ACTIVE", "pause": True})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_PAYLOAD_FIELD_REFUSED")

    def test_unsupported_kind_is_refused_by_name(self) -> None:
        inbox = _inbox("kind")
        refused = inbox.submit({"event_kind": "PROACTIVE_PING", "text": "x"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_EVENT_KIND_UNSUPPORTED")
        missing = inbox.submit({"text": "x"})
        self.assertEqual(missing["reason_code"], "CONTACT_EVENT_KIND_REQUIRED")


class CoPresenceCodecBridgeTest(unittest.TestCase):
    """严格的字段对齐：B 直接用事件块喂 ``life_co_presence``，不需要猜。"""

    def test_invitation_block_is_accepted_by_the_co_presence_codec(self) -> None:
        import life_co_presence as cop

        inbox = _inbox("codec-invitation")
        submitted = inbox.submit({
            "event_kind": "CO_PRESENCE_INVITATION", "scope": "一起坐着看书",
            "text": "我过来坐一会儿?", "occurred_at": WHEN,
            "expires_at": "2026-11-20T09:30:00+00:00", "duration_seconds": 1800,
            "requires_engagement": True, "source_ref": "channel:local",
        })
        self.assertTrue(submitted["ok"], submitted)
        self.assertEqual(submitted["effect"], lc.EFFECT_INVITATION)
        block = submitted["data"]["event"]["invitation"]
        parsed = cop.parse_invitation(block)
        self.assertEqual(parsed["invitation_id"], block["invitation_id"])
        self.assertEqual(parsed["scope"], "一起坐着看书")
        self.assertEqual(parsed["subject_id"], SUBJECT)
        self.assertEqual(parsed["home_ref"], HOME)
        self.assertEqual(parsed["requires_engagement"], True)
        self.assertEqual(parsed["atomic"], False)
        self.assertEqual(parsed["duration_seconds"], 1800)
        self.assertEqual(parsed["occurred_at"], WHEN)
        self.assertEqual(parsed["expires_at"], "2026-11-20T09:30:00+00:00")
        # Nothing the protocol needs is missing, and nothing extra is invented.
        self.assertEqual(set(parsed), {
            "invitation_id", "source_ref", "occurred_at", "expires_at", "scope",
            "subject_id", "home_ref", "requires_engagement", "atomic", "evidence_ref",
            "duration_seconds", "material_refs"})
        # The protocol's own classifier agrees that this is an invitation.
        verdict = cop.classify(block)
        self.assertEqual(verdict["effect"], cop.EFFECT_INVITATION)
        self.assertEqual(verdict["kind"], cop.KIND_INVITATION)

    def test_end_block_is_accepted_by_the_co_presence_codec(self) -> None:
        import life_co_presence as cop

        inbox = _inbox("codec-end")
        invitation = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                   "scope": "一起坐着看书", "occurred_at": WHEN})
        invitation_id = invitation["data"]["event"]["invitation_id"]
        submitted = inbox.submit({"event_kind": "CO_PRESENCE_END",
                                  "invitation_id": invitation_id,
                                  "explicit_end": True, "occurred_at": LATER,
                                  "reason": "EXPLICIT_END"})
        self.assertTrue(submitted["ok"], submitted)
        self.assertEqual(submitted["effect"], lc.EFFECT_END)
        block = submitted["data"]["event"]["end"]
        parsed = cop.parse_end(block)
        self.assertEqual(parsed["invitation_id"], invitation_id)
        self.assertEqual(parsed["explicit_end"], True)
        self.assertEqual(parsed["reason"], "EXPLICIT_END")
        self.assertEqual(cop.classify(block)["kind"], cop.KIND_END)
        # The end is already bound to the invitation *event* in this same write.
        binding = submitted["data"]["event"]["binding"]
        self.assertTrue(binding["resolved_at_submit"])
        self.assertEqual(binding["invitation_event_ref"], invitation["event_ref"])

    def test_end_without_explicit_evidence_is_refused(self) -> None:
        inbox = _inbox("end-evidence")
        invitation = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                   "scope": "坐坐", "occurred_at": WHEN})
        invitation_id = invitation["data"]["event"]["invitation_id"]
        refused = inbox.submit({"event_kind": "CO_PRESENCE_END",
                                "invitation_id": invitation_id, "occurred_at": LATER})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"],
                         "CONTACT_END_NEEDS_EXPLICIT_END_OR_COMPLETION_EVIDENCE")


class DeferAndTerminalTest(unittest.TestCase):
    """DEFER 必须具名；终态不复活。"""

    def test_defer_requires_a_named_condition(self) -> None:
        inbox = _inbox("defer")
        invitation = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                   "scope": "坐坐", "occurred_at": WHEN})
        claim = inbox.claim(invitation["event_ref"], consumer="life_cycle")
        bare = inbox.record_result(invitation["event_ref"], outcome="DEFERRED",
                                   claim_token=claim["claim_token"])
        self.assertFalse(bare["ok"])
        self.assertEqual(bare["reason_code"], "CONTACT_DEFER_CONDITION_REQUIRED")
        named = inbox.record_result(invitation["event_ref"], outcome="DEFERRED",
                                    claim_token=claim["claim_token"],
                                    opportunity_ref="opportunity:after-current-page")
        self.assertTrue(named["ok"], named)
        self.assertEqual(named["processing_state"], lc.STATE_DEFERRED)
        # A deferral is not something the next tick may re-ask: it is not pending.
        self.assertEqual(inbox.pending()["data"]["matched"], 0)
        deferred = inbox.deferred_events(opportunity_ref="opportunity:after-current-page")
        self.assertEqual(deferred["data"]["matched"], 1)
        self.assertEqual(inbox.deferred_events(condition="something-else")["data"]["matched"], 0)

    def test_terminal_result_never_revives(self) -> None:
        inbox = _inbox("terminal")
        invitation = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                   "scope": "坐坐", "occurred_at": WHEN})
        claim = inbox.claim(invitation["event_ref"], consumer="life_cycle")
        accepted = inbox.record_result(invitation["event_ref"], outcome="ACCEPTED",
                                       claim_token=claim["claim_token"],
                                       session_ref="co_presence:abc")
        self.assertEqual(accepted["processing_state"], lc.STATE_PROCESSED)
        again = inbox.record_result(invitation["event_ref"], outcome="ACCEPTED",
                                    claim_token=claim["claim_token"],
                                    session_ref="co_presence:abc")
        self.assertEqual(again["status"], "already_applied")
        self.assertFalse(again["replayed"] is False)
        changed = inbox.record_result(invitation["event_ref"], outcome="DECLINED",
                                      claim_token=claim["claim_token"])
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["reason_code"], "CONTACT_RESULT_TERMINAL")
        reclaim = inbox.claim(invitation["event_ref"], consumer="life_cycle")
        self.assertFalse(reclaim["ok"])
        self.assertEqual(reclaim["reason_code"], "CONTACT_EVENT_ALREADY_PROCESSED")

    def test_result_requires_the_claim_and_its_token(self) -> None:
        inbox = _inbox("claim-guard")
        invitation = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                   "scope": "坐坐", "occurred_at": WHEN})
        unclaimed = inbox.record_result(invitation["event_ref"], outcome="ACCEPTED")
        self.assertEqual(unclaimed["reason_code"], "CONTACT_EVENT_NOT_CLAIMED")
        claim = inbox.claim(invitation["event_ref"], consumer="life_cycle")
        wrong = inbox.record_result(invitation["event_ref"], outcome="ACCEPTED",
                                    claim_token="contact_claim:not-the-one")
        self.assertEqual(wrong["reason_code"], "CONTACT_CLAIM_MISMATCH")
        other = inbox.claim(invitation["event_ref"], consumer="another_consumer")
        self.assertFalse(other["ok"])
        self.assertEqual(other["status"], "claimed")
        self.assertEqual(other["claim"]["consumer"], "life_cycle")

    def test_ended_requires_the_invitation_it_closes(self) -> None:
        inbox = _inbox("ended")
        end = inbox.submit({"event_kind": "CO_PRESENCE_END",
                            "invitation_id": "localinvite:0000000000000000",
                            "explicit_end": True, "occurred_at": LATER})
        self.assertTrue(end["ok"], end)
        self.assertFalse(end["data"]["event"]["binding"]["resolved_at_submit"])
        claim = inbox.claim(end["event_ref"], consumer="life_cycle")
        refused = inbox.record_result(end["event_ref"], outcome="ENDED",
                                      claim_token=claim["claim_token"])
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_END_INVITATION_REQUIRED")


class ReadPurityTest(unittest.TestCase):
    """GET 必须是纯读：不建 lock、不改 revision、不做过期写。"""

    def test_reads_write_nothing_at_all(self) -> None:
        inbox = _inbox("pure")
        submitted = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                  "scope": "坐坐",
                                  "expires_at": "2020-01-01T09:00:00+00:00",
                                  "occurred_at": WHEN})
        event_ref = submitted["event_ref"]
        before = _digest_tree(inbox.state_root)
        self.assertIn(lc.STATE_FILENAME, before)

        inbox.summary()
        inbox.list_events()
        inbox.list_events(include_body=True)
        inbox.pending()
        inbox.claimed()
        inbox.deferred_events()
        inbox.describe()
        inbox.get_event(event_ref, include_body=True)
        inbox.operation(submitted["operation_key"])
        expired = inbox.get_event(event_ref)

        after = _digest_tree(inbox.state_root)
        self.assertEqual(before, after, "a read changed the state root")
        # The expiry is *derived* in the answer and never written back.
        self.assertTrue(expired["data"]["event"]["expired"])
        self.assertEqual(inbox.summary()["data"]["revision"],
                         json.loads(Path(lc.state_path(inbox.state_root))
                                    .read_text(encoding="utf-8"))["revision"])


    def test_a_read_on_an_absent_ledger_creates_no_files(self) -> None:
        inbox = _inbox("pure-absent")
        inbox.summary()
        inbox.list_events()
        inbox.pending()
        inbox.operation("contact_op:whatever0000000000000000")
        self.assertEqual(_digest_tree(inbox.state_root), {})


class CorruptStateTest(unittest.TestCase):
    """坏文件具名失败，绝不默默当空。"""

    def _seeded(self, name: str, raw: str) -> lc.LocalContactInbox:
        root = _root(name)
        inbox = lc.LocalContactInbox(root, subject_id=SUBJECT, home_ref=HOME)
        Path(lc.state_path(root)).write_text(raw, encoding="utf-8")
        return inbox

    def _assert_named(self, inbox: lc.LocalContactInbox, reason_code: str) -> None:
        for answer in (inbox.summary(), inbox.list_events(), inbox.pending(),
                       inbox.get_event("contact_evt:0000000000000000"),
                       inbox.operation("contact_op:000000000000000000000000")):
            self.assertFalse(answer["ok"], answer)
            self.assertEqual(answer["status"], "unreadable")
            self.assertEqual(answer["reason_code"], reason_code)
            self.assertIsNone(answer["data"])
        write = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "还能进吗"})
        self.assertFalse(write["ok"])
        self.assertEqual(write["reason_code"], reason_code)
        self.assertEqual(write["writes_performed"], 0)

    def test_garbage_json_is_corrupt(self) -> None:
        inbox = self._seeded("corrupt-garbage", '{"schema_version": "chiyo.life.local')
        self._assert_named(inbox, "CONTACT_STATE_CORRUPT")
        # The bad file is still exactly as it was: nothing was overwritten.
        self.assertEqual(Path(lc.state_path(inbox.state_root)).read_text(encoding="utf-8"),
                         '{"schema_version": "chiyo.life.local')

    def test_half_written_empty_file_is_named(self) -> None:
        inbox = self._seeded("corrupt-empty", "")
        self._assert_named(inbox, "CONTACT_STATE_EMPTY")

    def test_wrong_schema_is_named(self) -> None:
        inbox = self._seeded("corrupt-schema",
                             json.dumps({"schema_version": "chiyo.life.local_contact.v0",
                                         "events": {}, "operations": {}, "order": []}))
        self._assert_named(inbox, "CONTACT_STATE_SCHEMA_MISMATCH")

    def test_another_subject_owns_the_ledger(self) -> None:
        inbox = self._seeded("corrupt-subject",
                             json.dumps({"schema_version": lc.SCHEMA_VERSION,
                                         "subject_id": "someone-else", "home_ref": HOME,
                                         "revision": 1, "seq": 0, "events": {},
                                         "operations": {}, "order": []}))
        self._assert_named(inbox, "CONTACT_STATE_SUBJECT_MISMATCH")

    def test_missing_collections_are_corrupt(self) -> None:
        inbox = self._seeded("corrupt-shape",
                             json.dumps({"schema_version": lc.SCHEMA_VERSION,
                                         "subject_id": SUBJECT, "home_ref": HOME}))
        self._assert_named(inbox, "CONTACT_STATE_CORRUPT")


class BoundsTest(unittest.TestCase):
    """每一条上限都按名拒绝，不静默截断/丢弃。"""

    def test_body_too_long_is_refused(self) -> None:
        inbox = _inbox("bounds-body")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE",
                                "text": "x" * (lc.MAX_BODY_CHARS + 1)})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "CONTACT_BODY_TOO_LONG")
        self.assertEqual(inbox.summary()["data"]["event_count"], 0)

    def test_ambiguous_body_fields_are_refused(self) -> None:
        inbox = _inbox("bounds-ambiguous")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "a", "body": "b"})
        self.assertEqual(refused["reason_code"], "CONTACT_BODY_AMBIGUOUS")

    def test_capacity_refuses_instead_of_dropping_the_oldest(self) -> None:
        inbox = _inbox("bounds-capacity", max_events=2)
        first = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "1", "occurred_at": WHEN})
        second = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "2", "occurred_at": WHEN})
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        third = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "3", "occurred_at": WHEN})
        self.assertFalse(third["ok"])
        self.assertEqual(third["reason_code"], "CONTACT_INBOX_CAPACITY_REACHED")
        self.assertEqual(inbox.summary()["data"]["event_count"], 2)
        # The capacity answer reports the real numbers instead of an empty page.
        self.assertEqual(third["data"]["count"], 2)
        self.assertEqual(third["data"]["max_events"], 2)

    def test_list_limit_is_bounded_and_reported(self) -> None:
        inbox = _inbox("bounds-list")
        for index in range(4):
            inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "第%d条" % index,
                          "occurred_at": WHEN})
        page = inbox.list_events(limit=2)
        self.assertEqual(page["data"]["returned"], 2)
        self.assertEqual(page["data"]["matched"], 4)
        self.assertTrue(page["data"]["truncated"])
        self.assertEqual([row["body_chars"] for row in page["data"]["events"]],
                         [row["body_chars"] for row in page["data"]["events"]])

    def test_unknown_filter_is_refused(self) -> None:
        inbox = _inbox("bounds-filter")
        self.assertEqual(inbox.list_events(states=["NOT_A_STATE"])["reason_code"],
                         "CONTACT_STATE_FILTER_UNKNOWN")
        self.assertEqual(inbox.list_events(kinds=["NOT_A_KIND"])["reason_code"],
                         "CONTACT_KIND_FILTER_UNKNOWN")

    def test_a_bad_timestamp_is_refused(self) -> None:
        inbox = _inbox("bounds-time")
        refused = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "x",
                                "occurred_at": "yesterday-ish"})
        self.assertEqual(refused["reason_code"], "CONTACT_OCCURRED_AT_INVALID")


class DeferralClaimVisibilityTest(unittest.TestCase):
    """claim 是幂等的、跨消费者互斥的、崩溃后可见的（不假装还 pending）。"""

    def test_a_claim_hides_the_event_from_pending_until_it_settles(self) -> None:
        inbox = _inbox("claim-visibility")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "在吗",
                                  "occurred_at": WHEN})
        claim = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        self.assertEqual(claim["status"], "applied")
        self.assertEqual(inbox.pending()["data"]["matched"], 0)
        in_flight = inbox.claimed()["data"]["events"]
        self.assertEqual(len(in_flight), 1)
        self.assertEqual(in_flight[0]["claimed_by"], "life_cycle")
        self.assertEqual(in_flight[0]["attempts"], 1)
        # A crash leaves the claim standing; a restart still sees it.
        reopened = lc.LocalContactInbox(inbox.state_root, subject_id=SUBJECT, home_ref=HOME)
        self.assertEqual(len(reopened.claimed()["data"]["events"]), 1)
        self.assertEqual(reopened.pending()["data"]["matched"], 0)
        settled = reopened.record_result(submitted["event_ref"], outcome="OBSERVED",
                                         claim_token=claim["claim_token"])
        self.assertEqual(settled["status"], "applied")
        self.assertEqual(reopened.claimed()["data"]["matched"], 0)

    def test_the_same_consumer_reclaiming_is_idempotent(self) -> None:
        inbox = _inbox("claim-replay")
        submitted = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "在吗",
                                  "occurred_at": WHEN})
        first = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        again = inbox.claim(submitted["event_ref"], consumer="life_cycle")
        self.assertEqual(again["status"], "already_applied")
        self.assertEqual(again["claim_token"], first["claim_token"])


class DescribeTest(unittest.TestCase):
    """``describe()`` 自报边界：不是关系 owner、不是第二个 Activity owner。"""

    def test_describe_states_what_this_owner_is_not(self) -> None:
        described = _inbox("describe").describe()
        self.assertEqual(described["owner"], "LocalContactInbox")
        self.assertTrue(described["owns_transport_ledger"])
        self.assertFalse(described["owns_activity"])
        self.assertFalse(described["owns_relationship"])
        self.assertFalse(described["owns_conversation"])
        self.assertFalse(described["writes_on_read"])
        self.assertFalse(described["external_channel"])
        self.assertFalse(described["delivered_to_user"])
        self.assertTrue(described["local_only"])
        self.assertEqual(described["channel"], lc.CHANNEL_LOCAL_INBOX)
        self.assertEqual(set(described["kinds"]), set(lc.ALL_KINDS))


if __name__ == "__main__":
    unittest.main()
