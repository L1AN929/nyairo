#!/usr/bin/env python3
"""v0.4 处境到有限表达：被撤源/不可读时，草稿正文的扣留必须进入表达视图。

接缝（本单修复的那一处）
------------------------
``life_sharing_service.read_share`` 早已有既定规则：草稿正文只有在记录来源**此刻仍可读**
时才投影；来源被治理/拒绝或不可达时，整份返回不含正文，并给
``text_withheld`` / ``text_available`` / ``withheld_reason_code``。

``life_sharing_context._share_detail`` 是这份 owner 答案进入 Decision prompt 的唯一投影，
但它此前只把 ``ok == True`` 当成"详情可读"，把被扣留的正文投影成 ``text_excerpt: ""``
（看起来像"空草稿"），也没有把扣留事实与 owner 自己的原因码带进表达视图。
本文件先复现，再验证修好之后：

* 来源被撤权 → 表达视图明确 ``text_withheld``，正文不出现，原因码原样保留；
* 来源不可达 → 同样扣留，原因码不是 "NOT_FOUND"（隐私拒绝不伪装成未找到）；
* 来源正常 → 正文照常投影，``text_withheld`` 为 False；
* owner 没给扣留字段 → 不猜 True/False，保持 ``None``；
* 被扣留的草稿 **不会**被本地投递（owner 自己拒 STALE_SOURCE，零写）；
* 一次本地投递之后，重复发送不会造第二条 receipt；
* NO_ACTION / 延后 / 拒绝都不写投递；没有任何外发通道。

真实件：``ManagedArtifactStore``（真 sqlite / 真 CAS / 真 operation 账本）+
``PrivateProjectService`` + ``LifeSharingService`` + 真实 ``AutonomousLifeCycle``。
替身只有两处，且都明说：Decision 输入是脚本形状（不调模型）；"撤权/不可达"由端口对
**来源 artifact** 的读取被拒来模拟（父级既有探针同一做法）。不联网、不碰任何真实实例。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_cognition_adapter as aa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import life_sharing_context as lsc  # noqa: E402
import life_sharing_service as lss  # noqa: E402

FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04-situation-expression-"))

_LIFE_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
except Exception as error:  # noqa: BLE001
    _LIFE_ERROR = "%s: %s" % (type(error).__name__, error)

_OWNER_ERROR: str | None = None
try:
    from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402

    import importlib.util  # noqa: E402
    import private_project_service as pps  # noqa: E402

    _spec = importlib.util.spec_from_file_location(
        "situation_expression_owner_fixture",
        str(ROOT / "tests" / "test_v04_sharing_supply_owner.py"))
    _fixture = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_fixture)
    GRANT, SHARE_TEXT, SOURCE_TEXT = _fixture.GRANT, _fixture.SHARE_TEXT, _fixture.SOURCE_TEXT
    SUBJECT, WORKSPACE = _fixture.SUBJECT, _fixture.WORKSPACE
    Clock, SupplyStorePort, WsAuthority = (_fixture.Clock, _fixture.SupplyStorePort,
                                           _fixture.WsAuthority)
except Exception as error:  # noqa: BLE001
    _OWNER_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX：%s" % (_LIFE_ERROR or ""))
posix_only = unittest.skipIf(os.name == "nt" or _OWNER_ERROR is not None,
                             "真实 Supply owner 需要 POSIX：%s" % (_OWNER_ERROR or ""))


def _bare(value) -> str:
    return str(value or "").replace("artifact:", "")


class _SituationSource:
    def build(self, *, now, budget, observations):
        return {"reason_code": "NOT_WIRED"}


class _Runtime:
    def __init__(self, root):
        self.state_root = Path(root)
        self.world_opportunity_source = SimpleNamespace(
            record_observed_opportunity=lambda item: None,
            expire_opportunity=lambda ref, observed_at=None: None)
        self.candidate_read = None
        self.activity_read = SimpleNamespace(
            get_current_life_view=lambda: {"foreground_activity_ref": None,
                                           "foreground_activity_kind": None},
            get_activity=lambda ref: None, list_open_activities=lambda: [])
        self.ag2_progress_port = None
        self.world_action_adapter = None
        self.artifact_action_adapter = None
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})


class _SourceRefusingPort:
    """A real owner port whose **source** read is refused, for one named artifact only.

    This is the documented governed-source shape: ``read_project`` answers
    ``ok=False`` / ``rejected`` with the owner's own reason code.  Every other read (the share
    artifact itself, listings) goes to the real service unchanged.
    """

    def __init__(self, inner, *, source_id: str, status: str = "rejected",
                 reason_code: str = "SOURCE_GOVERNED", raise_error: bool = False) -> None:
        self._inner = inner
        self._source = _bare(source_id)
        self._status = status
        self._reason = reason_code
        self._raise = bool(raise_error)

    def read_project(self, artifact_id, **kwargs):
        if _bare(artifact_id) == self._source:
            if self._raise:
                raise RuntimeError("owner unreachable")
            return {"ok": False, "status": self._status, "reason_code": self._reason,
                    "detail": "the owner refused this source read"}
        return self._inner.read_project(artifact_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------------------
# 1. The projection itself: withheld is not empty, missing flags are not guessed
# ---------------------------------------------------------------------------------------


class _StubSharingService:
    """A minimal owner answer, so the projection can be exercised without any store."""

    def __init__(self, row: dict, detail: dict) -> None:
        self._row = dict(row)
        self._detail = dict(detail)

    def list_shares(self, *, include_text: bool = False, **kwargs):
        return {"ok": True, "status": "ok", "data": {
            "shares": [dict(self._row)], "counts": {}, "inbox": {}, "truncated": False}}

    def read_share(self, share_id, *, include_text: bool = True):
        return {"ok": True, "status": "ok", "data": dict(self._detail)}


class _WithholdingProjectionTests(unittest.TestCase):
    def _service(self, *, share_extra: dict = None, data_extra: dict = None,
                 flags: bool = True) -> _StubSharingService:
        share = {
            "share_id": "artifact:" + "a" * 32,
            "state": lss.STATE_DRAFT,
            "title": "留白重排",
            "head_version_id": "artifact-version:" + "b" * 32,
            "updated_at": "2026-10-24T12:00:00+00:00",
            "text": "",
            "text_chars": 42,
            "source": {"artifact_id": "artifact:" + "c" * 32,
                       "version_id": "artifact-version:" + "d" * 32},
            "deferred": None,
            "expires_at": None,
            "state_derived": False,
        }
        if flags:
            share.update({"text_withheld": True, "text_available": False})
        share.update(share_extra or {})
        data = {
            "share": share,
            "source_current": {"readable": False, "stale": True,
                               "matches_recorded": False, "reason_code": "SOURCE_GOVERNED"},
        }
        if flags:
            data.update({"text_withheld": True, "withheld_reason_code": "SOURCE_GOVERNED"})
        data.update(data_extra or {})
        return _StubSharingService({
            "share_id": share["share_id"], "state": share["state"], "title": share["title"],
            "head_version_id": share["head_version_id"],
            "updated_at": share["updated_at"], "delivered": False,
            "source": share["source"]}, data)

    def test_a_withheld_draft_is_named_and_its_words_do_not_appear(self):
        block = lsc.prompt_block(self._service())
        row = block["shares"][0]
        self.assertIs(row["text_withheld"], True)
        self.assertIs(row["text_available"], False)
        self.assertEqual(row["withheld_reason_code"], "SOURCE_GOVERNED")
        self.assertFalse(row["text_excerpt"])
        self.assertEqual(row["text_chars"], 42)  # length is not text; the owner keeps it
        # The adapter must carry the same fact and must not invent an excerpt.
        bounded = aa._bounded_sharing(block)
        entry = bounded["shares"][0]
        self.assertIs(entry["text_withheld"], True)
        self.assertIs(entry["text_available"], False)
        self.assertEqual(entry["withheld_reason_code"], "SOURCE_GOVERNED")
        self.assertNotIn("text_excerpt", entry)

    def test_a_readable_source_projects_the_draft_words(self):
        service = self._service(
            share_extra={"text": SHARE_TEXT, "text_withheld": False, "text_available": True},
            data_extra={"text_withheld": False, "withheld_reason_code": "",
                        "source_current": {"readable": True, "stale": False,
                                           "matches_recorded": True, "reason_code": ""}})
        row = lsc.prompt_block(service)["shares"][0]
        self.assertIs(row["text_withheld"], False)
        self.assertIs(row["text_available"], True)
        self.assertIn(SHARE_TEXT[:8], row["text_excerpt"])
        self.assertTrue(aa._bounded_sharing({"shares": [row]})["shares"][0]["text_excerpt"])

    def test_missing_withholding_flags_are_not_guessed(self):
        # An owner answer that says nothing about withholding: neither True nor False.
        service = self._service(
            share_extra={"text": SHARE_TEXT},
            data_extra={"source_current": {"readable": True, "stale": False,
                                           "matches_recorded": True, "reason_code": ""}},
            flags=False)
        row = lsc.prompt_block(service)["shares"][0]
        self.assertIsNone(row["text_withheld"])
        self.assertIsNone(row["text_available"])
        self.assertIn(SHARE_TEXT[:8], row["text_excerpt"])  # words present, no invented flag
        entry = aa._bounded_sharing({"shares": [row]})["shares"][0]
        self.assertNotIn("text_withheld", entry)
        self.assertNotIn("text_available", entry)


# ---------------------------------------------------------------------------------------
# 2. The real owner: governed/unreachable source, refusal writes nothing, one receipt only
# ---------------------------------------------------------------------------------------


@posix_only
@needs_life
class RealSourceWithholdingTests(unittest.TestCase):
    def setUp(self) -> None:
        stamp = "%s-%d-%d" % (self._testMethodName, os.getpid(), int(time.time() * 1000))
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.db_path = str(FIXTURE_ROOT / ("%s.sqlite" % stamp))
        self.clock = Clock()
        self.store = ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                          clock=self.clock)
        self.projects = pps.PrivateProjectService(SupplyStorePort(self.store), WORKSPACE, GRANT)
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)
        self.source = self._seed_source()
        root = Path(tempfile.mkdtemp(prefix="situation-expression-", dir=str(FIXTURE_ROOT)))
        self.cycle = alc.AutonomousLifeCycle(
            _Runtime(root), enabled=True, env={}, situation_source=_SituationSource(),
            work_artifact_id=self.source, sharing_service=self.service)
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()

    def _seed_source(self) -> str:
        created = self.store.create_artifact(
            subject_id=SUBJECT, workspace_id=WORKSPACE, title="她的留白练习",
            operation_key="expr-source-create", grant_reservation_id=GRANT)
        self.assertEqual(created.get("status"), "OK", created)
        artifact_id = str(created["artifact_id"])
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=SUBJECT, expected_head=None,
            content=SOURCE_TEXT, operation_key="expr-source-v1", grant_reservation_id=GRANT)
        self.assertEqual(committed.get("status"), "OK", committed)
        return artifact_id

    def _source_ref(self) -> str:
        return "artifact:%s" % _bare(self.source)

    def _next_round(self) -> None:
        # A real cycle re-reads the open drafts once per decision round; do the same before
        # every scripted action so a share created last round is actually offered this round.
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()

    def _act(self, raw: dict):
        action = ag1.validate_sharing_action(raw)
        result = self.cycle._apply_sharing_action(SimpleNamespace(sharing_action=action))
        return result

    def _draft(self) -> dict:
        drafted = self._act({"op": "prepare", "source_artifact_id": self._source_ref(),
                             "share_text": SHARE_TEXT, "title": "留白重排"})
        self.assertTrue(drafted.get("ok"), drafted)
        self.assertEqual(drafted.get("state"), lss.STATE_DRAFT)
        self.assertFalse(drafted.get("delivered"))
        self._next_round()
        return drafted

    def test_governed_source_withholds_words_and_refuses_the_send_without_writing(self):
        drafted = self._draft()
        share_id = drafted["share_id"]
        head = drafted["head_version_id"]

        governed = lss.LifeSharingService(
            _SourceRefusingPort(self.projects, source_id=self.source,
                                status="rejected", reason_code="SOURCE_GOVERNED"),
            clock=self.clock)

        # 1) The expression view names the withholding; the words are absent; the reason is
        #    the owner's own, not "not found".
        block = lsc.prompt_block(governed)
        row = [item for item in block["shares"] if item["share_id"] == share_id][0]
        self.assertIs(row["text_withheld"], True)
        self.assertIs(row["text_available"], False)
        self.assertEqual(row["withheld_reason_code"], "SOURCE_GOVERNED")
        self.assertNotIn("SOURCE_GOVERNED", (row.get("text_excerpt") or ""))
        self.assertFalse(row["text_excerpt"])
        self.assertTrue(row["source_current"]["readable"] is False)
        bounded = aa._bounded_sharing(dict(block, sources=block.get("sources") or []))
        bentry = [x for x in bounded["shares"] if x["share_id"] == share_id][0]
        self.assertIs(bentry["text_withheld"], True)
        self.assertNotEqual(bentry.get("withheld_reason_code"), "SHARING_SOURCE_NOT_FOUND")

        # 2) Sending a withheld draft is refused by the owner itself; no version is written.
        refused = lsc.apply_request(governed, share_id=share_id, action="send_local",
                                    expected_head=head)
        self.assertFalse(refused["ok"], refused)
        self.assertFalse(refused["delivered"])
        self.assertEqual(refused["state"], lss.STATE_DRAFT)
        after = lsc.read_draft(self.service, share_id)
        self.assertEqual(after["head_version_id"], head)
        self.assertEqual(after["state"], lss.STATE_DRAFT)
        self.assertIsNone(after["receipt"])

    def test_unreachable_source_withholds_with_a_distinct_reason(self):
        drafted = self._draft()
        share_id = drafted["share_id"]
        unreachable = lss.LifeSharingService(
            _SourceRefusingPort(self.projects, source_id=self.source,
                                status="unavailable", reason_code="OWNER_UNREACHABLE",
                                raise_error=True),
            clock=self.clock)
        row = [item for item in lsc.prompt_block(unreachable)["shares"]
               if item["share_id"] == share_id][0]
        self.assertIs(row["text_withheld"], True)
        self.assertIs(row["text_available"], False)
        self.assertTrue(row["withheld_reason_code"])
        self.assertNotEqual(row["withheld_reason_code"], "SHARING_SOURCE_NOT_FOUND")
        self.assertFalse(row["text_excerpt"])

    def test_one_local_delivery_and_a_repeat_never_makes_a_second_receipt(self):
        drafted = self._draft()
        share_id = drafted["share_id"]
        head = drafted["head_version_id"]
        sent = self._act({"op": "send_local", "share_id": share_id, "expected_head": head})
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertTrue(sent["delivered"])
        receipt = sent["receipt"]
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["external_delivery_confirmed"])
        self.assertFalse(receipt["read_by_audience"])
        self.assertFalse(receipt["understood"])

        current = lsc.read_draft(self.service, share_id)
        again = lsc.apply_request(self.service, share_id=share_id, action="send_local",
                                  expected_head=current["head_version_id"])
        self.assertFalse(again["ok"], again)
        self.assertFalse(again["delivered"])
        self.assertEqual(again["state"], lss.STATE_LOCAL_DELIVERED)
        after = lsc.read_draft(self.service, share_id)
        self.assertEqual(after["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertEqual(sent["delivery_count"], 1)
        # A delivered draft is no longer offered for action, but it stays readable.
        self.assertEqual(lsc.prompt_block(self.service)["shares"], [])

    def test_no_action_defer_and_refusal_write_no_delivery(self):
        drafted = self._draft()
        share_id, head = drafted["share_id"], drafted["head_version_id"]
        # Defer: state changes, but nothing is delivered and no receipt exists.
        deferred = self._act({"op": "defer", "share_id": share_id, "expected_head": head,
                              "opportunity_ref": "quiet-evening"})
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["state"], lss.STATE_DEFERRED)
        self.assertFalse(deferred["delivered"])
        after = lsc.read_draft(self.service, share_id)
        self.assertEqual(after["state"], lss.STATE_DEFERRED)
        self.assertIsNone(after["receipt"])
        self._next_round()
        # A stale head is refused by the owner's CAS; still no receipt, no state change.
        stale = self._act({"op": "send_local", "share_id": share_id, "expected_head": head})
        self.assertFalse(stale["ok"], stale)
        self.assertFalse(stale["delivered"])
        self.assertEqual(lsc.read_draft(self.service, share_id)["state"], lss.STATE_DEFERRED)
        self.assertIsNone(lsc.read_draft(self.service, share_id)["receipt"])

    def test_a_source_this_round_did_not_offer_is_refused_before_the_owner(self):
        before = lsc.prompt_block(self.service)["shares"]
        self.assertEqual(before, [])
        not_mine = "artifact:" + "f" * 32
        refused = self.cycle._apply_sharing_action(SimpleNamespace(sharing_action={
            "op": "prepare", "source_artifact_id": not_mine, "share_text": "x"}))
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], "SHARING_SOURCE_NOT_OFFERED")
        self.assertFalse(refused["delivered"])
        self.assertEqual(lsc.prompt_block(self.service)["shares"], [])

    def test_no_external_channel_exists_on_any_surface(self):
        block = self.cycle.sharing_decision_context()
        self.assertEqual(block["channel"], "local_inbox")
        self.assertEqual(block["audience"], "local:owner")
        self.assertTrue(block["inbox"]["local_only"])
        describe = self.service.describe()
        self.assertFalse(describe["external_send"])
        self.assertTrue(describe["owns_no_store"])
        self.assertEqual(describe["channels"], ["local_inbox"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
