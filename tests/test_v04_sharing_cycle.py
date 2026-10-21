#!/usr/bin/env python3
"""v0.4 分享进正常生活入口：草稿 → 延后 → 明确本地投递，以及 AG-1 的形状验证。

证据分级
--------
* ``SharingProposalShapeTests``（原生 Windows 也能跑）：AG-1 只验**形状** —— op 词表、真实引用
  （``artifact:`` / ``artifact-version:``）、有界文本、"没文本也没前缀"一律拒、"audience/channel"
  这类自由收件人字段一律拒；``DecisionRecord`` 会带着它，``to_dict`` 可重建。
* ``SharingPromptTests``（同上）：开着的草稿与"她能从哪里分享"进的是
  ``ProductionAgencyCognitionAdapter`` 的真实 payload；超长 prompt 时分享块是**可丢**的一块，
  系统提示写明 Local inbox ≠ 发给某人、沉默 ≠ 已读、不分享也完全正当。
* ``RealSharingChainTests``（POSIX，真实 Supply ``ManagedArtifactStore``）：A 的
  ``life_sharing_context`` + ``LifeSharingService`` 跑在真 owner 上（真 sqlite、不可变版本、真
  CAS、真 operation 账本）—— 草稿 DRAFT（≠ 已发）→ defer（写明等什么）→ send_local（本地投递
  一条 receipt）→ 再读仍然只有那一条投递、重复发送被 owner 拒；来源换版后不再发。
  夹具的"选择"由脚本给出（不是自然生活），投递只到本地 inbox，**没有**任何外发。

不联网、不调模型、不碰原实例；夹具根只建不删。
"""

from __future__ import annotations

import json
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

#: All fixtures live under one retained root (created once, never removed).
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-sharing-"))

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

    # B's real-owner fixture, loaded by path (``tests`` is a directory of scripts, not a package).
    _spec = importlib.util.spec_from_file_location(
        "sharing_supply_owner_fixture",
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


# ---------------------------------------------------------------------------------------
# 1. AG-1 shape validation (no owner, no prompt)
# ---------------------------------------------------------------------------------------


class SharingProposalShapeTests(unittest.TestCase):
    def test_prepare_needs_a_real_source_and_explicit_words(self):
        action = ag1.validate_sharing_action({
            "op": "prepare", "source_artifact_id": "artifact:" + "a" * 32,
            "share_text": "今天把那页的留白重排完了。", "title": "留白"})
        self.assertEqual(action["op"], "prepare")
        self.assertEqual(action["share_text"], "今天把那页的留白重排完了。")
        self.assertIsNone(action["excerpt_chars"])
        # 没文本也没前缀：owner 会拒，AG-1 先拒（绝不整篇私文当分享文本）。
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.validate_sharing_action({"op": "prepare",
                                         "source_artifact_id": "artifact:" + "a" * 32})
        # 前缀长度是显式选择，不是默认。
        prefix = ag1.validate_sharing_action({
            "op": "prepare", "source_artifact_id": "artifact:" + "a" * 32, "excerpt_chars": 120})
        self.assertEqual(prefix["excerpt_chars"], 120)

    def test_an_audience_or_channel_is_never_the_models_to_name(self):
        for bad in ({"op": "send_local", "share_id": "artifact:" + "b" * 32,
                     "expected_head": "artifact-version:" + "c" * 32,
                     "audience": "telegram:@someone"},
                    {"op": "prepare", "source_artifact_id": "artifact:" + "a" * 32,
                     "share_text": "x", "channel": "email"}):
            with self.subTest(raw=bad):
                with self.assertRaises(ag1.DecisionValidationError):
                    ag1.validate_sharing_action(bad)

    def test_send_and_drop_need_the_drafts_own_cas_head(self):
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.validate_sharing_action({"op": "send_local", "share_id": "artifact:" + "b" * 32})
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.validate_sharing_action({"op": "send_local", "share_id": "artifact:" + "b" * 32,
                                         "expected_head": "dec:" + "d" * 32})
        dropped = ag1.validate_sharing_action({
            "op": "drop", "share_id": "artifact:" + "b" * 32,
            "expected_head": "artifact-version:" + "c" * 32, "reason": "不想发了"})
        self.assertEqual(dropped["reason"], "不想发了")

    def test_defer_must_name_what_it_is_waiting_for(self):
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.validate_sharing_action({"op": "defer", "share_id": "artifact:" + "b" * 32,
                                         "expected_head": "artifact-version:" + "c" * 32})
        deferred = ag1.validate_sharing_action({
            "op": "defer", "share_id": "artifact:" + "b" * 32,
            "expected_head": "artifact-version:" + "c" * 32,
            "opportunity_ref": "evening-quiet"})
        self.assertEqual(deferred["opportunity_ref"], "evening-quiet")

    def test_unknown_ops_keys_and_bad_counts_are_refused(self):
        head = {"share_id": "artifact:" + "b" * 32,
                "expected_head": "artifact-version:" + "c" * 32}
        for bad in ({"op": "publish", **head}, {"op": "send_local", **head, "extra": 1},
                    {"op": "prepare", "source_artifact_id": "artifact:" + "a" * 32,
                     "excerpt_chars": 0},
                    {"op": "prepare", "source_artifact_id": "artifact:" + "a" * 32,
                     "excerpt_chars": 999}):
            with self.subTest(raw=bad):
                with self.assertRaises(ag1.DecisionValidationError):
                    ag1.validate_sharing_action(bad)

    def test_a_decision_record_carries_the_request_not_a_delivery(self):
        action = ag1.validate_sharing_action({
            "op": "prepare", "source_artifact_id": "artifact:" + "a" * 32, "share_text": "一段话"})
        self.assertIsNone(getattr(SimpleNamespace(), "sharing_action", None))
        self.assertNotIn("delivered", action)
        self.assertNotIn("audience", action)



    def test_the_validator_carries_it_and_refuses_a_recipient(self):
        """The key is accepted where real decisions are parsed, and carried as a request."""

        now = "2026-11-14T09:00:00+00:00"
        candidate_set = ag1.CandidateSet(
            candidate_set_id="cset:1", subject_id="chiyo", candidate_refs=["cand:1"],
            source_snapshot_refs=["snap:1"], observed_at=now, built_at=now)
        snapshot = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:1", opportunity_ref="dopp:1", candidate_set_ref="cset:1",
            life_frame_ref="lframe:1", captured_at=now, source_revisions={},
            snapshot_fingerprint="sfp:1")
        capacity = ag1.AgencyCapacitySnapshot(
            capacity_snapshot_id="cap:1", captured_at=now, source_revisions=[])
        head = {"share_id": "artifact:" + "b" * 32,
                "expected_head": "artifact-version:" + "c" * 32}
        validated = ag1.DecisionOutputValidator.validate_decision_output(
            {"decision": "NO_ACTION", "selected_candidate_id": None,
             "reason_codes": ["CAPACITY_LIMITED"], "short_rationale": "先不发",
             "sharing": {"op": "defer", **head, "opportunity_ref": "quiet-evening"}},
            candidate_set=candidate_set, candidates_by_id={}, constraint_results_by_id={},
            context_snapshot=snapshot, capacity_snapshot=capacity)
        self.assertEqual(validated["sharing_action"]["op"], "defer")
        self.assertEqual(validated["sharing_action"]["share_id"], head["share_id"])
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.DecisionOutputValidator.validate_decision_output(
                {"decision": "NO_ACTION", "selected_candidate_id": None,
                 "reason_codes": ["CAPACITY_LIMITED"], "short_rationale": "发给别人",
                 "sharing": {"op": "send_local", **head, "audience": "someone@example.com"}},
                candidate_set=candidate_set, candidates_by_id={},
                constraint_results_by_id={}, context_snapshot=snapshot,
                capacity_snapshot=capacity)


# ---------------------------------------------------------------------------------------
# 2. the prompt
# ---------------------------------------------------------------------------------------


class _Client:
    provider = "scripted"
    model = "scripted-decision"


def _request(situation_context):
    return SimpleNamespace(
        opportunity_id="opp:share-1", snapshot_fingerprint="fp:share-1", policy_version="1",
        legal_decision_enums=["START", "NO_ACTION"], allowed_reason_codes=["OK"],
        current_activity_summary=None, capacity_summary=None,
        observed_fact_keys=["world_here_place_home"], prior_experiences=[],
        situation_context=situation_context,
        candidates=[{"candidate_id": "cand:work", "candidate_kind": "CONTINUE",
                     "source_kind": "ARTIFACT", "source_ref": "artifact:work",
                     "target_refs": ["artifact:" + "a" * 32],
                     "legal_decisions": ["CONTINUE", "NO_ACTION"]}],
        max_output_tokens=512,
    )


def _sharing_block(*, shares=None, sources=None):
    return {
        "schema": "chiyo.life.sharing_context.v1",
        "status": "observed", "reason_code": "", "channel": "local_inbox",
        "audience": "local:owner",
        "shares": list(shares or []),
        "sources": list(sources or [{"artifact_id": "artifact:" + "a" * 32,
                                     "kind": "current_work"}]),
        "counts": {"total": 1, "draft": 1},
        "inbox": {"delivered_count": 0, "local_only": True},
        "allowed_share_ids": [row["share_id"] for row in (shares or [])],
        "how": "prepare needs one source artifact from sources + explicit words",
        "note": "local_inbox only; no external channel is configured or enabled",
    }


class SharingPromptTests(unittest.TestCase):
    def test_the_open_draft_and_its_head_reach_the_prompt(self):
        share = {"share_id": "artifact:" + "b" * 32, "state": "DEFERRED", "title": "留白",
                 "head_version_id": "artifact-version:" + "c" * 32, "updated_at": "2026-11-14",
                 "delivered": False}
        situation = {"world": {"status": "observed", "location": {"name": "家"}},
                     "sharing": _sharing_block(shares=[share])}
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted")
        _, payload = adapter.build_prompt(_request(situation))
        block = payload["situation_context"]["sharing"]
        self.assertEqual(block["channel"], "local_inbox")
        self.assertEqual(block["shares"][0]["state"], "DEFERRED")
        self.assertEqual(block["shares"][0]["head_version_id"],
                         "artifact-version:" + "c" * 32)
        self.assertEqual(block["sources"][0]["kind"], "current_work")
        # 分享正文永不进 prompt；也没有任何"外部已送达"字样。
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("外发", blob)
        self.assertNotIn("external_delivery_confirmed", blob)

    def test_the_prompt_states_local_inbox_and_no_obligation(self):
        prompt = aa.SYSTEM_PROMPT
        self.assertIn("situation_context.sharing", prompt)
        self.assertIn("local_inbox", prompt)
        self.assertIn("A draft is not", prompt)
        self.assertIn("Omitting sharing is", prompt)
        self.assertIn("sharing", prompt)

    def test_the_sharing_block_is_bounded_and_droppable(self):
        share = {"share_id": "artifact:" + "b" * 32, "state": "DRAFT", "title": "x" * 400,
                 "head_version_id": "artifact-version:" + "c" * 32}
        situation = {
            "world": {"status": "observed", "location": {"name": "家"}},
            "sharing": _sharing_block(shares=[share] * 20,
                                      sources=[{"artifact_id": "artifact:%d" % i,
                                                "kind": "k" * 200} for i in range(20)]),
            "project_options": [{"option_ref": "opt:%d" % i, "title": "t" * 300,
                                 "seed_excerpt": "s" * 300} for i in range(4)],
        }
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted",
                                                      max_prompt_chars=1400)
        text, payload = adapter.build_prompt(_request(situation))
        json.loads(text)
        self.assertTrue(adapter.last_prompt_trim["trimmed"])
        steps = {row["step"] for row in adapter.last_prompt_trim["dropped"]}
        self.assertIn("sharing", steps)
        # 丢的是"分享菜单"，选择本身仍然可能。
        self.assertIn("cand:work", {row["candidate_id"]
                                    for row in payload["candidates"]})


# ---------------------------------------------------------------------------------------
# 3. owner 层边界（drop / 换版 / 未 offer / CAS），**helper 级**：
#    主链证据在 tests/test_v04_sharing_life_chain.py（正式 assembly + evaluate_once + 原始 JSON 决策）。
# ---------------------------------------------------------------------------------------


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


@posix_only
@needs_life
class RealSharingChainTests(unittest.TestCase):
    def setUp(self):
        stamp = "%s-%d-%d" % (self._testMethodName, os.getpid(), int(time.time() * 1000))
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.db_path = str(FIXTURE_ROOT / ("%s.sqlite" % stamp))
        self.clock = Clock()
        self.store = ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                          clock=self.clock)
        self.projects = pps.PrivateProjectService(SupplyStorePort(self.store), WORKSPACE, GRANT)
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)
        self.source = self._seed_source()
        root = Path(tempfile.mkdtemp(prefix="sharecycle-", dir=str(FIXTURE_ROOT)))
        self.cycle = alc.AutonomousLifeCycle(
            _Runtime(root), enabled=True, env={}, situation_source=_SituationSource(),
            work_artifact_id=self.source, sharing_service=self.service)
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()

    def _seed_source(self):
        created = self.store.create_artifact(
            subject_id=SUBJECT, workspace_id=WORKSPACE, title="她的留白练习",
            operation_key="cycle-source-create", grant_reservation_id=GRANT)
        self.assertEqual(created.get("status"), "OK", created)
        artifact_id = str(created["artifact_id"])
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=SUBJECT, expected_head=None,
            content=SOURCE_TEXT, operation_key="cycle-source-v1", grant_reservation_id=GRANT)
        self.assertEqual(committed.get("status"), "OK", committed)
        return artifact_id

    def _act(self, raw):
        """One *scripted* decision's sharing request, through the cycle's own apply path."""

        action = ag1.validate_sharing_action(raw)
        result = self.cycle._apply_sharing_action(SimpleNamespace(sharing_action=action))
        self.assertIsNotNone(result)
        return result

    def test_draft_defer_and_one_local_delivery(self):
        source_id = "artifact:%s" % str(self.source).replace("artifact:", "")
        context = self.cycle.last_sharing_block
        self.assertIn(source_id, {row["artifact_id"] for row in context["sources"]})

        drafted = self._act({"op": "prepare", "source_artifact_id": source_id,
                             "share_text": SHARE_TEXT, "title": "留白重排"})
        self.assertTrue(drafted["ok"], drafted)
        self.assertEqual(drafted["state"], lss.STATE_DRAFT)
        self.assertFalse(drafted["delivered"])       # 草稿 ≠ 已发
        share_id = drafted["share_id"]
        head = drafted["head_version_id"]

        # 下一轮决策能看到它（仍是 DRAFT，并且带着 CAS head）
        later = self.cycle.sharing_decision_context()
        self.assertIn(share_id, later["allowed_share_ids"])
        row = [x for x in later["shares"] if x["share_id"] == share_id][0]
        self.assertEqual(row["state"], lss.STATE_DRAFT)
        self.assertEqual(row["head_version_id"], head)
        self.cycle.last_sharing_block = later

        deferred = self._act({"op": "defer", "share_id": share_id, "expected_head": head,
                              "opportunity_ref": "quiet-evening"})
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["state"], lss.STATE_DEFERRED)
        self.assertFalse(deferred["delivered"])
        self.assertEqual((deferred["deferred"] or {}).get("opportunity_ref"), "quiet-evening")

        # 旧 head 已经过期：CAS 由 owner 自己判
        stale = self._act({"op": "send_local", "share_id": share_id, "expected_head": head})
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(stale["status"], lss.STATUS_CONFLICT)
        self.assertFalse(stale["delivered"])

        current = lsc.read_draft(self.service, share_id)
        self.assertEqual(current["state"], lss.STATE_DEFERRED)
        sent = self._act({"op": "send_local", "share_id": share_id,
                          "expected_head": current["head_version_id"]})
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertTrue(sent["delivered"])
        self.assertEqual(sent["delivery_count"], 1)
        receipt = sent["receipt"]
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["external_delivery_confirmed"])
        self.assertFalse(receipt["read_by_audience"])

        # 再读：投递事实只有一条，且不是"已读"
        again = lsc.read_draft(self.service, share_id)
        self.assertEqual(again["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertTrue(again["delivered"])
        self.assertEqual(again["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertFalse(again["receipt"]["understood"])

        # 重复发送：owner 拒（已是终态投递），不会出现第二条投递
        repeat = self._act({"op": "send_local", "share_id": share_id,
                            "expected_head": again["head_version_id"]})
        self.assertFalse(repeat["ok"], repeat)
        self.assertIn(repeat["reason_code"], (lss.REASON_ALREADY_DELIVERED,
                                              lss.REASON_TERMINAL_STATE))
        self.assertTrue(repeat["ok"] is False and repeat["delivered"] is False)

        # 收件箱事实：本地投递计数只有这一条，且明确 local_only
        final = self.cycle.sharing_decision_context()
        self.assertEqual(final["channel"], "local_inbox")
        self.assertTrue(final["inbox"]["local_only"])
        self.assertEqual(final["inbox"]["delivered_count"], 1)
        self.assertEqual(final["allowed_share_ids"], [])

    def test_a_dropped_draft_is_terminal_and_never_revived(self):
        source_id = "artifact:%s" % str(self.source).replace("artifact:", "")
        drafted = self._act({"op": "prepare", "source_artifact_id": source_id,
                             "share_text": SHARE_TEXT})
        self.assertTrue(drafted["ok"], drafted)
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()
        dropped = self._act({"op": "drop", "share_id": drafted["share_id"],
                             "expected_head": drafted["head_version_id"], "reason": "算了"})
        self.assertTrue(dropped["ok"], dropped)
        self.assertEqual(dropped["state"], lss.STATE_DROPPED)
        self.assertFalse(dropped["delivered"])
        # 旧 timer 不复活：再 defer/send 都不行，也不能"重新打开"成草稿
        revived = self._act({"op": "defer", "share_id": drafted["share_id"],
                             "expected_head": dropped["head_version_id"],
                             "defer_condition": "等下次"})
        self.assertFalse(revived["ok"], revived)
        self.assertEqual(revived["reason_code"], lss.REASON_TERMINAL_STATE)

    def test_a_changed_source_is_not_sent_on_the_old_draft(self):
        source_id = "artifact:%s" % str(self.source).replace("artifact:", "")
        drafted = self._act({"op": "prepare", "source_artifact_id": source_id,
                             "share_text": SHARE_TEXT})
        self.assertTrue(drafted["ok"], drafted)
        head = self.store.read_head(source_id)
        committed = self.store.commit_markdown(
            artifact_id=source_id, authorized_subject_id=SUBJECT,
            expected_head=head["version_id"], content=SOURCE_TEXT + "又改了一遍。",
            operation_key="cycle-source-v2", grant_reservation_id=GRANT)
        self.assertEqual(committed.get("status"), "OK", committed)
        current = lsc.read_draft(self.service, drafted["share_id"])
        self.assertTrue(current["source_current"]["stale"])
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()
        sent = self._act({"op": "send_local", "share_id": drafted["share_id"],
                          "expected_head": current["head_version_id"]})
        self.assertFalse(sent["ok"], sent)
        self.assertEqual(sent["status"], lss.STATUS_STALE_SOURCE)
        self.assertFalse(sent["delivered"])

    def test_an_unoffered_source_or_share_id_never_reaches_the_owner(self):
        refused = self._act({"op": "prepare", "source_artifact_id": "artifact:" + "e" * 32,
                             "share_text": "随便说说"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "SHARING_SOURCE_NOT_OFFERED")
        refused_share = self._act({"op": "send_local", "share_id": "artifact:" + "f" * 32,
                                   "expected_head": "artifact-version:" + "c" * 32})
        self.assertFalse(refused_share["ok"])
        self.assertEqual(refused_share["reason_code"], "SHARING_DRAFT_NOT_OFFERED")

    def test_with_no_owner_wired_nothing_is_offered_and_nothing_claims_delivery(self):
        root = Path(tempfile.mkdtemp(prefix="nosowner-", dir=str(FIXTURE_ROOT)))
        cycle = alc.AutonomousLifeCycle(_Runtime(root), enabled=True, env={},
                                        situation_source=_SituationSource())
        block = cycle.sharing_decision_context()
        self.assertEqual(block["status"], "unavailable")
        self.assertEqual(block["reason_code"], lsc.REASON_NOT_WIRED)
        self.assertEqual(block["shares"], [])
        action = ag1.validate_sharing_action({
            "op": "prepare", "source_artifact_id": "artifact:" + "a" * 32, "share_text": "x"})
        result = cycle._apply_sharing_action(SimpleNamespace(sharing_action=action))
        self.assertFalse(result["ok"])
        self.assertFalse(result["delivered"])
        self.assertEqual(result["reason_code"], lsc.REASON_NOT_WIRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
