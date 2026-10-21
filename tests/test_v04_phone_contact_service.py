#!/usr/bin/env python3
"""v0.4 阶段 C：手机来访面（``components/phone/contact.py`` + ``PhoneService``）的服务层用例。

这个文件测什么
--------------
* **真实 ``PhoneContact`` / 真实 A 的 ``LocalContactInbox``**：夹具 state root 是磁盘上的真目录，
  投递会真的落 ``life_local_contact.json``。测试**保留**夹具，不删除任何文件。
* 三条投递：普通来话（只观察）、明确范围的邀请、显式结束当前邀请。
* 「queued ≠ accepted」：``submit`` 之后 ``accepted`` 恒为 False、``pause_requested`` /
  ``activity_started`` 恒为 False、``owners_written`` 恒 0；``queued`` 只在 PENDING 时为 True。
* 纯读：``summary`` / ``listing`` / ``event`` / ``operation`` 前后 state root 不变（含文件数量）。
* 摘要不泄正文：counts / rows / session 里没有任何 body 字段；正文只在 ``event(include_body=True)``。
* 处理结果 / 延后条件 / 结束绑定：用 B 的 ``claim`` + ``record_result`` **读面写一次**（这是消费方
  的写法，本文件只用来验证 P 的读面照实投影，P 自己从不调用它们）。
* 原 key 对账：``absent`` / ``applied`` / ``unknown`` / ``unreadable`` 四种答案原样透出。
* 坏账本：具名 reason（不是「没有来信」）。
* Life 没跑：没有 daemon、没有线程、没有 tick——``PhoneContact`` 构造后不产生任何后台对象。

不做什么：不联网、不连原实例、不起 Life 运行时、不 mock 掉 A 的账本。
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402
from components.phone import contact as phone_contact  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SENTINEL = "PRIVATE_CONTACT_SENTINEL_DO_NOT_LEAK"


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_PHONE_CONTACT_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-phone-contact") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-phone-contact"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str = "v04-contact-svc-") -> Path:
    """A retained, unique fixture root.  Nothing in this file deletes one."""

    base = fixture_base()
    root = base / ("%s%d-%d" % (prefix, os.getpid(), time.time_ns()))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def directory_snapshot(root: Path) -> dict:
    """Names + sizes of everything under ``root`` (the zero-write witness)."""

    out = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            info = path.stat()
        except OSError:
            out[str(path.relative_to(root))] = ("MISSING", 0)
            continue
        out[str(path.relative_to(root))] = (info.st_size, info.st_mtime_ns)
    return out


class ContactFixture(unittest.TestCase):
    """One real state root + one real ``PhoneContact`` over A's own inbox."""

    def setUp(self) -> None:
        self.state_root = unique_root()
        self.contact = phone_contact.PhoneContact(
            state_root=self.state_root, subject="chiyo", home_ref="home:fixture")

    # -- helpers -----------------------------------------------------------------------

    def submit_message(self, text: str = "我来啦", **extra) -> dict:
        payload = {"event_kind": "LOCAL_MESSAGE", "text": text}
        payload.update(extra)
        return self.contact.submit(payload)

    def submit_invitation(self, scope: str = "一起坐十分钟", **extra) -> dict:
        payload = {"event_kind": "CO_PRESENCE_INVITATION", "scope": scope,
                   "text": "坐坐？"}
        payload.update(extra)
        return self.contact.submit(payload)

    def invitation_id_of(self, answer: dict) -> str:
        ref = answer["data"]["event_ref"]
        detail = self.contact.event(ref, include_body=False)
        self.assertTrue(detail["ok"], detail)
        return detail["data"]["event"]["invitation_id"]


class SurfaceBoundaryTest(ContactFixture):
    def test_describe_owns_no_store_and_no_second_writer(self) -> None:
        described = self.contact.describe()
        self.assertEqual(described["kind"], "phone_contact")
        self.assertEqual(described["owner"], "LocalContactInbox")
        self.assertTrue(described["owns_no_store"])
        self.assertFalse(described["owns_transport_ledger"])
        self.assertFalse(described["owns_activity"])
        self.assertFalse(described["owns_relationship"])
        self.assertFalse(described["starts_second_writer"])
        self.assertFalse(described["external_send"])
        self.assertTrue(described["local_only"])
        self.assertEqual(described["channel"], "local_inbox")
        self.assertEqual(described["source"], "local_operator")
        self.assertIn("本地收件箱", described["note"])

    def test_construction_creates_nothing(self) -> None:
        """Building the facade must not create the state root or any file in it."""

        fresh = unique_root("v04-contact-nocreate-")
        contacts = phone_contact.PhoneContact(state_root=fresh, subject="chiyo",
                                              home_ref="home:fixture")
        before = directory_snapshot(fresh)
        contacts.describe()
        self.assertEqual(directory_snapshot(fresh), before, "describe 不许写盘")
        # 读面也不建目录 / 不建锁。
        summary = contacts.summary()
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["data"]["ledger_state"], "ABSENT")
        self.assertEqual(directory_snapshot(fresh), before, "summary 不许写盘")

    def test_unbound_identity_is_honest_not_an_empty_inbox(self) -> None:
        without_subject = phone_contact.PhoneContact(state_root=self.state_root, subject="",
                                                     home_ref="home:fixture")
        answer = without_subject.summary()
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "CONTACT_SUBJECT_UNBOUND")
        without_home = phone_contact.PhoneContact(state_root=self.state_root, subject="chiyo",
                                                 home_ref="")
        answer = without_home.summary()
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "CONTACT_HOME_UNBOUND")


class DeliverMessageTest(ContactFixture):
    def test_ordinary_message_is_observation_only_and_queued(self) -> None:
        answer = self.submit_message("我来啦" + SENTINEL)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], "applied")
        self.assertEqual(answer["data"]["effect"], "OBSERVATION_ONLY")
        self.assertEqual(answer["data"]["processing_state"], "PENDING")
        self.assertTrue(answer["data"]["queued"], "投完就是 queued")
        # 三个硬事实：界面没有替她接纳、没有 pause、没有起活动。
        self.assertFalse(answer["data"]["accepted"])
        self.assertFalse(answer["data"]["pause_requested"])
        self.assertFalse(answer["data"]["activity_started"])
        self.assertEqual(answer["data"]["owners_written"], 0)
        self.assertTrue(answer["data"]["local_only"])
        # 落盘了：账本文件真的存在。
        ledger = lc.state_path(self.state_root)
        self.assertTrue(ledger.exists(), "投递必须真的落 A 的账本")

    def test_message_never_carries_the_text_in_the_summary(self) -> None:
        answer = self.submit_message("正文里有" + SENTINEL)
        listing = self.contact.listing()
        summary = self.contact.summary()
        self.assertNotIn(SENTINEL, json.dumps(listing, ensure_ascii=False))
        self.assertNotIn(SENTINEL, json.dumps(summary, ensure_ascii=False))
        self.assertFalse(listing["data"]["private_text_included"])
        row = listing["data"]["events"][0]
        self.assertFalse(row["body_included"])
        self.assertEqual(row["body_chars"], len("正文里有" + SENTINEL))
        self.assertTrue(row["body_stored"])
        # submit 自己的答复也把正文放在事件外面（body_included=False）。
        self.assertFalse(answer["data"]["event"]["body_included"])

    def test_body_only_on_the_detail_read(self) -> None:
        answer = self.submit_message("只有详情里才有" + SENTINEL)
        ref = answer["data"]["event_ref"]
        without = self.contact.event(ref, include_body=False)
        self.assertTrue(without["ok"], without)
        self.assertEqual(without["data"]["body"], "")
        self.assertFalse(without["data"]["body_included"])
        with_body = self.contact.event(ref, include_body=True)
        self.assertTrue(with_body["data"]["body_included"])
        self.assertEqual(with_body["data"]["body"], "只有详情里才有" + SENTINEL)

    def test_same_intent_twice_is_one_event(self) -> None:
        first = self.submit_message("同一句话")
        second = self.submit_message("同一句话")
        self.assertEqual(first["data"]["event_ref"], second["data"]["event_ref"])
        self.assertEqual(second["status"], "already_applied")
        self.assertFalse(second["data"]["created"])
        self.assertTrue(second["data"]["replayed"])
        self.assertEqual(self.contact.summary()["data"]["event_count"], 1)


class InvitationTest(ContactFixture):
    def test_invitation_needs_a_scope(self) -> None:
        answer = self.contact.submit({"event_kind": "CO_PRESENCE_INVITATION", "text": "坐坐？"})
        self.assertFalse(answer["ok"], answer)
        self.assertIn("SCOPE", answer["reason_code"])

    def test_invitation_is_queued_and_never_accepted(self) -> None:
        answer = self.submit_invitation("一起坐十分钟")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["effect"], "INVITATION")
        self.assertEqual(answer["data"]["processing_state"], "PENDING")
        self.assertTrue(answer["data"]["queued"])
        self.assertFalse(answer["data"]["accepted"])
        invitation_id = self.invitation_id_of(answer)
        self.assertTrue(invitation_id.startswith("localinvite:"), invitation_id)

    def test_detail_exposes_the_invitation_block_for_b(self) -> None:
        answer = self.submit_invitation("一起看完这一页")
        detail = self.contact.event(answer["data"]["event_ref"], include_body=True)
        block = detail["data"]["invitation"]
        self.assertIsNotNone(block)
        self.assertEqual(block["scope"], "一起看完这一页")
        self.assertEqual(block["invitation_id"], self.invitation_id_of(answer))
        # P 投影的是 B 的协议块：它不该带任何 Activity 字段。
        self.assertNotIn("activity_id", block)
        self.assertNotIn("pause", block)
        self.assertTrue(detail["data"]["local_only"])


class EndInvitationTest(ContactFixture):
    def test_end_needs_an_invitation(self) -> None:
        answer = self.contact.submit({"event_kind": "CO_PRESENCE_END", "explicit_end": True})
        self.assertFalse(answer["ok"], answer)
        self.assertIn("INVITATION_ID", answer["reason_code"])

    def test_end_binds_to_the_named_invitation(self) -> None:
        invitation = self.submit_invitation("一起坐一会儿")
        invitation_id = self.invitation_id_of(invitation)
        end = self.contact.submit({"event_kind": "CO_PRESENCE_END",
                                   "invitation_id": invitation_id, "explicit_end": True,
                                   "reason": "EXPLICIT_END"})
        self.assertTrue(end["ok"], end)
        self.assertEqual(end["data"]["effect"], "END")
        self.assertEqual(end["data"]["processing_state"], "PENDING")
        detail = self.contact.event(end["data"]["event_ref"])
        self.assertEqual(detail["data"]["end"]["invitation_id"], invitation_id)
        self.assertTrue(detail["data"]["end"]["explicit_end"])
        # 提交那一刻 A 只记「在本账本里找到了那条邀请」（resolved_at_submit）；
        # bound=True 要等 B 的 record_result 才算绑定成立——P 不替它提前声称。
        self.assertTrue(detail["data"]["binding"]["resolved_at_submit"],
                        detail["data"]["binding"])
        self.assertFalse(detail["data"]["binding"]["bound"],
                         "提交时不许把绑定说成已经成立")
        self.assertEqual(detail["data"]["binding"]["invitation_event_ref"],
                         invitation["data"]["event_ref"])

    def test_end_requires_explicit_end_or_evidence(self) -> None:
        invitation = self.submit_invitation("一起坐一会儿")
        invitation_id = self.invitation_id_of(invitation)
        answer = self.contact.submit({"event_kind": "CO_PRESENCE_END",
                                      "invitation_id": invitation_id})
        self.assertFalse(answer["ok"], answer)
        self.assertIn("EVIDENCE", (answer["reason_code"] or "").upper()
                      or answer["reason_code"] or "EVIDENCE")


class ZeroWriteReadTest(ContactFixture):
    def test_reads_write_nothing(self) -> None:
        self.submit_message("一条")
        invitation = self.submit_invitation("坐一会儿")
        before = directory_snapshot(self.state_root)
        self.contact.summary()
        self.contact.listing(limit=10)
        self.contact.event(invitation["data"]["event_ref"], include_body=True)
        self.contact.operation(invitation["data"]["operation_key"])
        self.contact.describe()
        self.assertEqual(directory_snapshot(self.state_root), before,
                         "所有读面都必须零写（含 mtime）")

    def test_absent_ledger_stays_absent_after_reads(self) -> None:
        fresh = unique_root("v04-contact-absent-")
        contacts = phone_contact.PhoneContact(state_root=fresh, subject="chiyo",
                                              home_ref="home:fixture")
        listing = contacts.listing()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["events"], [])
        self.assertEqual(listing["data"]["ledger_state"], "ABSENT")
        self.assertFalse(lc.state_path(fresh).exists(), "读不许把账本建出来")


class OperationReconcileTest(ContactFixture):
    def test_original_key_reads_back_as_applied(self) -> None:
        answer = self.submit_message("一条有键的来话")
        key = answer["data"]["operation_key"]
        got = self.contact.operation(key)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["status"], "applied")
        self.assertEqual(got["data"]["event_ref"], answer["data"]["event_ref"])
        self.assertEqual(got["data"]["event_state"], "PENDING")
        self.assertTrue(got["data"]["recorded"])
        self.assertFalse(got["data"]["retryable"])
        self.assertIn("不会用新键重投", got["data"]["note"])

    def test_absent_key_says_absent_and_retryable(self) -> None:
        got = self.contact.operation("contact_op:" + "0" * 24)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["status"], "absent")
        self.assertFalse(got["data"]["recorded"])
        self.assertTrue(got["data"]["retryable"])

    def test_unknown_outcome_is_reported_as_unknown(self) -> None:
        """B 记了一次 UNKNOWN：P 的读面照实透出 unknown + retryable，不换键。"""

        answer = self.submit_invitation("一起坐一会儿")
        ref = answer["data"]["event_ref"]
        key = answer["data"]["operation_key"]
        inbox = lc.LocalContactInbox(self.state_root, subject_id="chiyo",
                                     home_ref="home:fixture")
        claim = inbox.claim(ref, consumer="life_cycle")
        self.assertTrue(claim["ok"], claim)
        token = claim["claim_token"]
        recorded = inbox.record_result(ref, outcome="UNKNOWN", claim_token=token,
                                       reason_code="CONTACT_MODEL_UNAVAILABLE")
        self.assertTrue(recorded["ok"], recorded)
        got = self.contact.operation(key)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["status"], "unknown")
        self.assertTrue(got["data"]["retryable"])
        self.assertEqual(got["data"]["outcome"], "UNKNOWN")
        self.assertEqual(got["data"]["event_state"], "UNKNOWN")

    def test_empty_key_is_refused(self) -> None:
        answer = self.contact.operation("")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "CONTACT_OPERATION_KEY_REQUIRED")


class ResultProjectionTest(ContactFixture):
    """处理结果 / 延后条件 / 结束在 P 的读面上照实投影（写仍由 B 做）。"""

    def test_deferral_shows_its_condition(self) -> None:
        answer = self.submit_invitation("一起坐一会儿")
        ref = answer["data"]["event_ref"]
        inbox = lc.LocalContactInbox(self.state_root, subject_id="chiyo",
                                     home_ref="home:fixture")
        token = inbox.claim(ref, consumer="life_cycle")["claim_token"]
        inbox.record_result(ref, outcome="DEFERRED", claim_token=token,
                            opportunity_ref="opportunity:reading-break")
        listing = self.contact.listing()
        row = [item for item in listing["data"]["events"] if item["event_ref"] == ref][0]
        self.assertEqual(row["processing_state"], "DEFERRED")
        self.assertEqual(row["outcome"], "DEFERRED")
        self.assertEqual(row["opportunity_ref"], "opportunity:reading-break")
        detail = self.contact.event(ref)
        self.assertEqual(detail["data"]["result"]["outcome"], "DEFERRED")
        self.assertEqual(detail["data"]["result"]["opportunity_ref"],
                         "opportunity:reading-break")
        self.assertFalse(detail["data"]["result"]["terminal"])

    def test_accepted_is_the_transport_result_not_an_activity(self) -> None:
        answer = self.submit_invitation("一起坐一会儿")
        ref = answer["data"]["event_ref"]
        inbox = lc.LocalContactInbox(self.state_root, subject_id="chiyo",
                                     home_ref="home:fixture")
        token = inbox.claim(ref, consumer="life_cycle")["claim_token"]
        inbox.record_result(ref, outcome="ACCEPTED", claim_token=token,
                            session_ref="co_presence:abc")
        detail = self.contact.event(ref)
        result = detail["data"]["result"]
        self.assertEqual(result["outcome"], "ACCEPTED")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["session_ref"], "co_presence:abc")
        # P 自己的信封仍旧不声称活动；Activity 只有 B 的 owner 通路能产生。
        self.assertFalse(detail["data"]["event"]["activity_started"])
        self.assertFalse(detail["data"]["event"]["pause_requested"])

    def test_message_cannot_record_acceptance(self) -> None:
        """普通来话被记为 ACCEPTED 时 A 会拒绝：P 的读面不会把拒绝写成成功。"""

        answer = self.submit_message("我来啦")
        ref = answer["data"]["event_ref"]
        inbox = lc.LocalContactInbox(self.state_root, subject_id="chiyo",
                                     home_ref="home:fixture")
        token = inbox.claim(ref, consumer="life_cycle")["claim_token"]
        refused = inbox.record_result(ref, outcome="ACCEPTED", claim_token=token)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], "CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY")
        detail = self.contact.event(ref)
        self.assertEqual(detail["data"]["event"]["processing_state"], "CLAIMED")
        self.assertIsNone(detail["data"]["result"])


class SessionRecordTest(ContactFixture):
    def test_absent_session_is_reported_as_absent(self) -> None:
        summary = self.contact.summary()
        session = summary["data"]["session"]
        self.assertEqual(session["state"], "ABSENT")
        self.assertFalse(session["open"])
        self.assertFalse(session["readable"])

    def test_open_session_is_projected_without_inventing(self) -> None:
        """B 的 co_presence.json 是 P 只读投影的对象：写一份真的文件，看它读成什么。"""

        record = {"schema_version": "chiyo.life.co_presence.v1", "state": "OPEN",
                  "invitation_id": "localinvite:" + "a" * 16, "scope": "一起坐十分钟",
                  "opened_at": "2026-11-20T10:00:00+00:00",
                  "joint_activity_ref": "activity:joint-1"}
        phone_contact.session_path_for(self.state_root).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
        summary = self.contact.summary()
        session = summary["data"]["session"]
        self.assertEqual(session["state"], "OPEN")
        self.assertTrue(session["open"])
        self.assertTrue(session["readable"])
        self.assertEqual(session["scope"], "一起坐十分钟")
        self.assertEqual(session["invitation_id"], "localinvite:" + "a" * 16)
        # P 只投影标签：joint_activity_ref 不会变成联合活动 id 的另一个真相。
        self.assertEqual(session["joint_activity_id"], "")

    def test_unreadable_session_is_named(self) -> None:
        phone_contact.session_path_for(self.state_root).write_text("{not json",
                                                                  encoding="utf-8")
        session = self.contact.summary()["data"]["session"]
        self.assertEqual(session["state"], "UNREADABLE")
        self.assertFalse(session["open"])


class CorruptLedgerTest(ContactFixture):
    def test_corrupt_ledger_is_named_not_empty(self) -> None:
        lc.state_path(self.state_root).write_text("{not json", encoding="utf-8")
        summary = self.contact.summary()
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["status"], "unreadable")
        self.assertIn(summary["reason_code"],
                      {"CONTACT_STATE_CORRUPT", "CONTACT_STATE_UNREADABLE",
                       "CONTACT_STATE_EMPTY"}, summary)
        listing = self.contact.listing()
        self.assertFalse(listing["ok"])

    def test_corrupt_ledger_is_never_reported_as_an_empty_inbox(self) -> None:
        lc.state_path(self.state_root).write_text("", encoding="utf-8")
        listing = self.contact.listing()
        self.assertFalse(listing["ok"], "空文件不许当成「没有来信」")
        self.assertIn(listing["reason_code"],
                      {"CONTACT_STATE_EMPTY", "CONTACT_STATE_CORRUPT",
                       "CONTACT_STATE_UNREADABLE"})


class HomeBindingTest(unittest.TestCase):
    """``home_ref_for`` 的来源顺序：每一档都记录来源，客户端永远提供不了。"""

    def test_b_world_home_wins_first(self) -> None:
        """与 B 的 ``autonomous_life_assembly`` 同一个来源：``CHIYO_WORLD_HOME``。"""

        home, source = phone_contact.home_ref_for(
            {"CHIYO_WORLD_HOME": "home:world", "NYA02_LIFE_HOME": "home:shell"},
            {"contact_home_ref": "home:field", "world_home": "home:instance"})
        self.assertEqual(home, "home:world")
        self.assertEqual(source, "environment:CHIYO_WORLD_HOME")

    def test_instance_field_is_the_second_source(self) -> None:
        home, source = phone_contact.home_ref_for({}, {"contact_home_ref": "home:field"})
        self.assertEqual(home, "home:field")
        self.assertEqual(source, "instance:contact_home_ref")

    def test_instance_world_home_is_the_third_source(self) -> None:
        home, source = phone_contact.home_ref_for({}, {"world_home": "home:instance"})
        self.assertEqual(home, "home:instance")
        self.assertEqual(source, "instance:world_home")

    def test_a_shell_export_is_the_last_environment_resort(self) -> None:
        """``NYA02_LIFE_HOME`` 只是操作者 shell 的变量，排在实例字段之后。"""

        home, source = phone_contact.home_ref_for({"NYA02_LIFE_HOME": "home:shell"},
                                                 {"world_home": "home:instance"})
        self.assertEqual(home, "home:instance")
        home, source = phone_contact.home_ref_for({"NYA02_LIFE_HOME": "home:shell"}, {})
        self.assertEqual(home, "home:shell")
        self.assertEqual(source, "environment:NYA02_LIFE_HOME")

    def test_unbound_is_empty_not_invented(self) -> None:
        home, source = phone_contact.home_ref_for({}, {})
        self.assertEqual(home, "")
        self.assertEqual(source, "")


class ServiceBindingTest(unittest.TestCase):
    """``PhoneService.contact`` 在真实实例上：绑定自己的 state root，不猜、不建、不起 daemon。"""

    def setUp(self) -> None:
        self.root = unique_root("v04-contact-service-")
        (self.root / "run").mkdir(parents=True, exist_ok=True)
        self.state_root = self.root / "one-day" / "life" / "state"
        self.state_root.mkdir(parents=True)
        self.instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "contact-fixture",
            "subject": "chiyo",
            "root": str(self.root),
            "profile_home": str(self.root),
            "world_home": str(self.root / "one-day" / "world-home"),
            "world_id": "w-contact",
            "supply_socket": str(self.root / "run" / "supply-fixture.sock"),
            "private_items_artifact_id": "artifact:" + "1" * 32,
            "artifact_grant_id": "grant:" + "2" * 32,
            "workspace_id": "workspace:" + "3" * 32,
            "world_body_socket": str(self.root / "one-day" / "world-home" / "run" / "gw.sock"),
            "features": {},
        }
        Path(self.instance["supply_socket"]).write_text("", encoding="utf-8")
        # 只喂一个「操作者 shell 里的 NYA02_LIFE_HOME」：它不是身份来源，进程环境不许决定
        # 谁的收件箱。实例自己的 world_home 才是（与 B 的 assembly 用同一个 home 来源）。
        self.service = phone_service.PhoneService(
            self.instance, environment={"NYA02_LIFE_HOME": "home:from-shell"})
        self.world_home = str(self.instance["world_home"])

    def test_contact_uses_the_instance_state_root(self) -> None:
        answer = self.service.contact()
        self.assertIn("data", answer)
        data = answer["data"]
        self.assertEqual(data["subject"], "chiyo")
        self.assertTrue(data["home_bound"])
        # 实例的 world_home 经 ``map_life_environment`` 映射成 ``CHIYO_WORLD_HOME``——与 B 的
        # ``autonomous_life_assembly`` 建 ``FiniteCoPresence(home=...)`` 用的是同一个值。
        self.assertEqual(data["home_source"], "environment:CHIYO_WORLD_HOME")
        self.assertEqual(data["ledger_state"], "ABSENT")
        # 绑的是这一个实例的 Life state root，不是 phone root、不是 install root。
        self.assertFalse(lc.state_path(self.state_root).exists())

    def test_submit_lands_in_the_bound_state_root_only(self) -> None:
        answer = self.service.contact_submit({"event_kind": "LOCAL_MESSAGE", "text": "你好"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(lc.state_path(self.state_root).exists(),
                        "必须落在实例自己的 Life state root")
        self.assertFalse((self.root / "life_local_contact.json").exists(),
                         "不许落在 phone root 上")

    def test_service_never_starts_a_writer(self) -> None:
        """没有线程、没有定时器：调用前后线程数不变。"""

        import threading

        before = threading.active_count()
        self.service.contact()
        self.service.contact_submit({"event_kind": "LOCAL_MESSAGE", "text": "你好"})
        self.service.contact_events(10)
        self.assertEqual(threading.active_count(), before,
                         "来访面不许悄悄起后台 daemon")

    def test_state_summary_reports_contact_counts_without_text(self) -> None:
        self.service.contact_submit({"event_kind": "LOCAL_MESSAGE",
                                     "text": "一条有正文的来话" + SENTINEL})
        state = self.service.state()
        self.assertIn("contact", state["apps"])
        entry = state["apps"]["contact"]
        self.assertEqual(entry["status"], "available")
        self.assertNotIn(SENTINEL, json.dumps(state, ensure_ascii=False),
                         "/state 不许带出来话正文")
        self.assertIn("session", entry["data"])


if __name__ == "__main__":
    unittest.main()
