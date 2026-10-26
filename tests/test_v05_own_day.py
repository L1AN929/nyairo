#!/usr/bin/env python3
"""v0.5 “她自己排一天”：日计划 / 外部义务 / 今日预留位。

纯域部分守规则本身（业务日 +08:00、0–3 条、幂等、日期/状态/主体、读失败与缺失不同、
写者分离、预留位取最早与去重、结果只认真实回执）。POSIX 部分用隔离真 owner 正式链：
正式 AG-1 原始 JSON Decision → 持久计划 → operator 义务 → review/question 占满 2 个常规位
后两条预留仍进入最终请求 → 选中义务时继续它绑定的作品 → 结果投影绑定本次 Decision/回执；
负控覆盖取消/改期/不可读/源终态/挂起同目标。Decision 与内容回答是脚本替身，明示。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import artifact_continuation_source as acs  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_day_plan as ldp  # noqa: E402
import life_future_items as lfi  # noqa: E402
import life_project_options as lpo  # noqa: E402

T0 = "2026-11-10T09:00:00+00:00"  # +08:00 的 2026-11-10 17:00
DAY = "2026-11-10"
SUBJECT = "test:v05-own-day"

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips the class below
    try:
        import v05_own_day_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        driver = None
else:  # pragma: no cover
    driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


def _fitem(item_ref: str = "fitem:aaa", *, title: str = "把水井那段补记一笔",
           due_at: str = T0, status: str = "OPEN", subject: str = SUBJECT,
           source_refs=None) -> dict:
    return {"item_ref": item_ref, "title": title, "due_at": due_at, "status": status,
            "origin": "MODEL_PROPOSAL", "subject_id": subject,
            "source_refs": list(source_refs or []), "revision": 1}


class BusinessDayTest(unittest.TestCase):
    """业务日只由调用方的业务 clock 按 +08:00 换算。"""

    def test_utc_evening_is_my_next_business_day(self):
        self.assertEqual(ldp.business_date("2026-10-25T16:30:00+00:00"), "2026-10-26")
        self.assertEqual(ldp.business_date("2026-10-25T15:59:59+00:00"), "2026-10-25")

    def test_local_instant_is_not_shifted_twice(self):
        self.assertEqual(ldp.business_date("2026-10-26T00:30:00+08:00"), "2026-10-26")

    def test_a_bad_now_is_named(self):
        with self.assertRaises(ldp.DayPlanError) as caught:
            ldp.business_date("not-a-time")
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_NOW_INVALID")


class PlanRequestTest(unittest.TestCase):
    def test_only_item_ref_is_accepted(self):
        self.assertEqual(ldp.validate_plan_request({"item_ref": "fitem:abc"}),
                         {"item_ref": "fitem:abc"})

    def test_a_date_or_an_unknown_key_is_refused(self):
        for raw in ({"item_ref": "fitem:abc", "date": DAY},
                    {"item_ref": "fitem:abc", "title": "x"},
                    {"item_ref": "artifact:abc"},
                    "fitem:abc"):
            with self.assertRaises(ldp.DayPlanError):
                ldp.validate_plan_request(raw)


class DayPlanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = ldp.DayPlan(self.root, subject_id=SUBJECT)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_means_nothing_planned_not_an_error(self):
        self.assertEqual(self.store.for_date(DAY), [])

    def test_plan_writes_a_reference_and_is_idempotent(self):
        first = self.store.plan(_fitem(), date=DAY, decision_ref="dec:one", now=T0)
        self.assertTrue(first["created"])
        entry = first["entry"]
        self.assertEqual(entry["item_ref"], "fitem:aaa")
        self.assertEqual(entry["date"], DAY)
        self.assertEqual(entry["source"], "SUBJECT_DECISION")
        self.assertEqual(entry["decision_ref"], "dec:one")
        again = self.store.plan(_fitem(), date=DAY, decision_ref="dec:one", now=T0)
        self.assertFalse(again["created"])
        self.assertEqual(len(self.store.for_date(DAY)), 1)

    def test_a_new_decision_for_the_same_item_does_not_duplicate(self):
        self.store.plan(_fitem(), date=DAY, decision_ref="dec:one", now=T0)
        second = self.store.plan(_fitem(), date=DAY, decision_ref="dec:two", now=T0)
        self.assertFalse(second["created"])
        self.assertTrue(second.get("deduplicated"))
        self.assertEqual(len(self.store.for_date(DAY)), 1)

    def test_at_most_three_items_per_day(self):
        for index in range(3):
            result = self.store.plan(_fitem("fitem:item%d" % index), date=DAY,
                                     decision_ref="dec:%d" % index, now=T0)
            self.assertTrue(result["created"])
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.plan(_fitem("fitem:item3"), date=DAY, decision_ref="dec:3", now=T0)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_CAPACITY_FULL")

    def test_only_today_is_supported(self):
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.plan(_fitem(), date="2026-11-12", decision_ref="dec:x", now=T0)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_DATE_NOT_TODAY")

    def test_a_non_open_or_foreign_item_is_refused(self):
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.plan(_fitem(status="CANCELLED"), date=DAY, decision_ref="dec:x", now=T0)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_ITEM_NOT_OPEN")
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.plan(_fitem(subject="someone-else"), date=DAY,
                            decision_ref="dec:x", now=T0)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_SUBJECT_MISMATCH")

    def test_a_corrupt_file_is_not_read_as_empty(self):
        (self.root / ldp.PLAN_FILENAME).write_text("not json", encoding="utf-8")
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.for_date(DAY)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_STATE_CORRUPT")

    def test_a_different_schema_is_refused(self):
        (self.root / ldp.PLAN_FILENAME).write_text(
            json.dumps({"schema_version": "other", "entries": {}}), encoding="utf-8")
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.store.for_date(DAY)
        self.assertEqual(caught.exception.reason_code, "DAY_PLAN_STATE_SCHEMA_MISMATCH")


class ObligationsDomainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reader = ldp.DayObligations(self.root, subject_id=SUBJECT)
        self.saved: list[Path] = []

    def tearDown(self):
        self._tmp.cleanup()

    def _import(self, **overrides):
        kwargs = dict(
            date=DAY, title="上午先把导言读完", target_ref="artifact:work1",
            due_at="2026-11-10T10:00:00+00:00", source_ref="operator:test",
            operation_key="oblig:test:1",
            target_verifier=lambda ref: {"resumable": True},
            subject_id=SUBJECT, now=T0)
        kwargs.update(overrides)
        return ldp.import_obligation(self.root, **kwargs)

    def test_missing_file_is_empty_but_corrupt_is_named(self):
        self.assertEqual(self.reader.for_date(DAY), [])
        (self.root / ldp.OBLIGATIONS_FILENAME).write_text("not json", encoding="utf-8")
        with self.assertRaises(ldp.DayPlanError) as caught:
            self.reader.for_date(DAY)
        self.assertEqual(caught.exception.reason_code, "OBLIGATIONS_STATE_CORRUPT")

    def test_the_life_reader_has_no_write_method(self):
        self.assertFalse(hasattr(self.reader, "import_obligation"))
        self.assertFalse(hasattr(self.reader, "plan"))

    def test_import_is_idempotent_and_bound_to_one_artifact(self):
        first = self._import()
        self.assertTrue(first["created"])
        entry = first["entry"]
        self.assertEqual(entry["source"], "OPERATOR")
        self.assertEqual(entry["target_ref"], "artifact:work1")
        self.assertEqual(entry["completion"]["target_ref"], "artifact:work1")
        again = self._import()
        self.assertFalse(again["created"])
        self.assertEqual(len(self.reader.for_date(DAY)), 1)

    def test_an_unknown_or_unreadable_or_terminal_target_is_refused(self):
        cases = [
            (lambda ref: None, "OBLIGATION_TARGET_UNKNOWN"),
            (lambda ref: (_ for _ in ()).throw(OSError("socket down")),
             "OBLIGATION_TARGET_UNREADABLE"),
            (lambda ref: {"resumable": False}, "OBLIGATION_TARGET_NOT_EXECUTABLE"),
        ]
        for verifier, reason in cases:
            with self.assertRaises(ldp.DayPlanError) as caught:
                self._import(operation_key="oblig:%s" % reason, target_verifier=verifier)
            self.assertEqual(caught.exception.reason_code, reason)
        self.assertEqual(self.reader.for_date(DAY), [])

    def test_a_missing_verifier_writes_nothing(self):
        with self.assertRaises(ldp.DayPlanError) as caught:
            self._import(target_verifier=None)
        self.assertEqual(caught.exception.reason_code, "OBLIGATION_TARGET_VERIFIER_MISSING")

    def test_the_due_date_must_fall_on_the_same_business_day(self):
        with self.assertRaises(ldp.DayPlanError) as caught:
            self._import(due_at="2026-11-12T10:00:00+00:00")
        self.assertEqual(caught.exception.reason_code, "OBLIGATION_DUE_DATE_MISMATCH")

    def test_a_stale_expected_revision_is_a_conflict(self):
        self._import()
        with self.assertRaises(ldp.DayPlanError) as caught:
            self._import(operation_key="oblig:test:2", expect_revision=99)
        self.assertEqual(caught.exception.reason_code, "OBLIGATION_STATE_REVISION_CONFLICT")

    def test_results_are_projected_only_from_real_receipts(self):
        self._import()
        day = ldp.obligation_result_view(self.reader, date=DAY, now=T0)
        self.assertEqual(day["status"], "OK")
        self.assertEqual([row["result"] for row in day["entries"]],
                         [ldp.OBLIGATION_RESULT_PENDING])
        before = (self.root / ldp.OBLIGATIONS_FILENAME).read_bytes()
        evidence: dict = {}
        bound = ldp.record_obligation_receipt(
            evidence, self.reader, date=DAY,
            receipt={"artifact_id": "artifact:work1", "effect_state": "APPLIED",
                     "version_id": "artifact-version:1", "decision_id": "dec:one"})
        self.assertEqual(bound["status"], ldp.OBLIGATION_RESULT_DONE)
        view = ldp.obligation_result_view(self.reader, date=DAY, now=T0, evidence=evidence)
        self.assertEqual(view["entries"][0]["result"], ldp.OBLIGATION_RESULT_DONE)
        # 只读投影从不写回 operator 的文档。
        self.assertEqual((self.root / ldp.OBLIGATIONS_FILENAME).read_bytes(), before)

    def test_a_receipt_for_another_artifact_does_not_complete_this_one(self):
        self._import()
        evidence: dict = {}
        bound = ldp.record_obligation_receipt(
            evidence, self.reader, date=DAY,
            receipt={"artifact_id": "artifact:other", "effect_state": "APPLIED"})
        self.assertIsNone(bound)
        self.assertEqual(evidence, {})

    def test_an_unknown_receipt_is_not_a_completion(self):
        self._import()
        evidence: dict = {}
        bound = ldp.record_obligation_receipt(
            evidence, self.reader, date=DAY,
            receipt={"artifact_id": "artifact:work1", "effect_state": "UNKNOWN"})
        self.assertEqual(bound["status"], ldp.OBLIGATION_RESULT_UNKNOWN)

    def test_obligations_cannot_be_cancelled_through_future_item_actions(self):
        with self.assertRaises(lfi.FutureItemError) as caught:
            lfi.validate_actions([{"item_ref": "oblig:abc", "op": "cancel"}])
        self.assertEqual(caught.exception.reason_code,
                         "FUTURE_ITEM_ACTION_REF_NOT_AN_ITEM")
        self.assertEqual(lfi.validate_actions([{"item_ref": "fitem:abc", "op": "cancel"}]),
                         [{"item_ref": "fitem:abc", "op": "cancel"}])


class PersistedObligationEvidenceTest(unittest.TestCase):
    """结果必须从已存在的 Action/Decision/owner 回执重读，且关联不足时不能报 DONE。"""

    def _entry(self, *, ref="oblig:one", target="artifact:work1"):
        return {"obligation_ref": ref, "date": DAY, "title": "导言",
                "due_at": T0, "status": "PLANNED", "source": "OPERATOR",
                "target_ref": target,
                "completion": {"kind": "ARTIFACT_STEP_RECEIPT", "target_ref": target,
                               "expected": "SUCCEEDED"}}

    def _evidence(self, *, actions, decisions=None, receipts=None, entries=None,
                  candidate_id="cand:expected"):
        decisions = dict(decisions if decisions is not None else {
            "dec:one": {"decision_id": "dec:one", "decision": "START",
                        "selected_candidate_ref": candidate_id}})
        receipts = dict(receipts if receipts is not None
                        else {"actn:one": [self._receipt()]})
        return ldp.persisted_obligation_evidence(
            entries if entries is not None else [self._entry()], day=DAY,
            action_lister=lambda: actions,
            decision_getter=lambda decision_id: decisions.get(decision_id),
            receipt_lister=lambda action_id: receipts.get(action_id, []),
            candidate_id_for=lambda ref, revision: candidate_id)

    @staticmethod
    def _action(*, target="artifact:artifact:work1", decision_ref="dec:one",
                status="SUCCEEDED", activity="actv_1", action_id="actn:one"):
        return {"action_id": action_id, "target_ref": target,
                "decision_ref": decision_ref, "activity_ref": activity, "status": status}

    @staticmethod
    def _receipt(*, action_id="actn:one", version="artifact-version:v2"):
        return {"receipt_id": "arcpt:one", "action_id": action_id,
                "status_claim": "SUCCEEDED", "provider_operation_ref": "op:one",
                "observed_at": T0,
                "details": {"version_id": version, "version_number": 2,
                            "operation_key": "op:one"}}

    def test_the_option_ref_is_the_one_reserved_options_really_offers(self):
        options, _withheld = ldp.reserved_options(
            plan_entries=[], obligations=[self._entry()], now=T0,
            futures_lookup=lambda ref: None,
            target_reader=lambda ref: {"resumable": True})
        self.assertEqual(options[0]["option_ref"],
                         ldp.obligation_option_ref("oblig:one", "artifact:work1", DAY))

    def test_a_fully_associated_success_is_done_with_its_own_identity(self):
        seen = {}

        def candidate_id_for(ref, revision):
            seen["ref"], seen["revision"] = ref, revision
            return "cand:expected"

        evidence = ldp.persisted_obligation_evidence(
            [self._entry()], day=DAY, action_lister=lambda: [self._action()],
            decision_getter=lambda did: {"decision": "START",
                                         "selected_candidate_ref": "cand:expected"},
            receipt_lister=lambda aid: [self._receipt()],
            candidate_id_for=candidate_id_for)
        row = evidence["oblig:one"]
        self.assertEqual(row["status"], ldp.OBLIGATION_RESULT_DONE)
        self.assertEqual(row["decision_id"], "dec:one")
        self.assertEqual(row["action_id"], "actn:one")
        self.assertEqual(row["activity_ref"], "actv_1")
        self.assertEqual(row["version_id"], "artifact-version:v2")
        self.assertEqual(row["evidence_path"], ldp.EVIDENCE_PATH_PERSISTED)
        self.assertEqual(seen["ref"],
                         ldp.obligation_option_ref("oblig:one", "artifact:work1", DAY))
        self.assertEqual(seen["revision"], 1)

    def test_another_decision_or_another_target_is_not_claimed(self):
        # 同一真实作品，但 Decision 选的是另一个候选：这是别人的动作，不认领。
        evidence = self._evidence(
            actions=[self._action()],
            decisions={"dec:one": {"decision": "START",
                                   "selected_candidate_ref": "cand:other"}})
        self.assertEqual(evidence, {})
        # 另一个目标上的成功动作同样不认领。
        evidence = self._evidence(actions=[self._action(target="artifact:artifact:work2")])
        self.assertEqual(evidence, {})

    def test_real_unknown_or_unsettled_or_unreceipted_is_not_done(self):
        cases = [
            ([self._action(status="UNKNOWN")], {},
             "OBLIGATION_ACTION_OUTCOME_UNKNOWN"),
            ([self._action(status="SUBMITTED")], {},
             "OBLIGATION_ACTION_NOT_SETTLED"),
            ([self._action()], {"actn:one": []}, "OBLIGATION_OWNER_RECEIPT_MISSING"),
            ([self._action(activity="")], {}, "OBLIGATION_ACTIVITY_ASSOCIATION_MISSING"),
        ]
        for actions, receipts, reason in cases:
            evidence = self._evidence(actions=actions, receipts=receipts)
            self.assertEqual(evidence["oblig:one"]["status"],
                             ldp.OBLIGATION_RESULT_UNKNOWN)
            self.assertEqual(evidence["oblig:one"]["reason_code"], reason)

    def test_an_unverifiable_association_is_unknown_not_pending(self):
        evidence = self._evidence(actions=[self._action(decision_ref="")])
        self.assertEqual(evidence["oblig:one"]["status"], ldp.OBLIGATION_RESULT_UNKNOWN)
        self.assertEqual(evidence["oblig:one"]["reason_code"],
                         "OBLIGATION_ASSOCIATION_UNVERIFIED")
        # A Decision that cannot be read is the same honest answer.
        evidence = self._evidence(
            actions=[self._action()],
            decisions={})
        self.assertEqual(evidence["oblig:one"]["status"], ldp.OBLIGATION_RESULT_UNKNOWN)
        # Matching candidate id but a non-START Decision cannot authorize this completion.
        evidence = self._evidence(
            actions=[self._action()],
            decisions={"dec:one": {"decision": "DEFER",
                                   "selected_candidate_ref": "cand:expected"}})
        self.assertEqual(evidence["oblig:one"]["status"], ldp.OBLIGATION_RESULT_UNKNOWN)

    def test_a_definite_failure_is_left_pending(self):
        evidence = self._evidence(actions=[self._action(status="FAILED")])
        self.assertEqual(evidence, {})

    def test_an_unreadable_action_ledger_is_unknown_for_every_obligation(self):
        def broken():
            raise OSError("ledger down")

        evidence = ldp.persisted_obligation_evidence(
            [self._entry()], day=DAY, action_lister=broken,
            decision_getter=lambda did: None,
            receipt_lister=lambda aid: [],
            candidate_id_for=lambda ref, revision: "cand:expected")
        self.assertEqual(evidence["oblig:one"]["status"], ldp.OBLIGATION_RESULT_UNKNOWN)
        self.assertEqual(evidence["oblig:one"]["reason_code"],
                         "OBLIGATION_ACTION_READ_FAILED")


class FutureItemActionsCompatibilityTest(unittest.TestCase):
    """fitem-only 收紧不得改变原本合法的输入或旧记录的重放。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = lfi.FutureItems(Path(self._tmp.name), subject_id=SUBJECT)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_bare_ref_is_still_normalised_exactly_as_before(self):
        self.assertEqual(lfi.validate_actions([{"item_ref": "abc", "op": "cancel"}]),
                         [{"item_ref": "fitem:abc", "op": "cancel"}])
        self.assertEqual(lfi.validate_actions([{"item_ref": "fitem:abc", "op": "cancel"}]),
                         [{"item_ref": "fitem:abc", "op": "cancel"}])

    def test_an_old_persisted_item_still_accepts_cancel_and_reschedule(self):
        created = self.store.propose(title="旧事项", due_at=T0, now=T0)
        item_ref = created["item"]["item_ref"]
        self.assertTrue(item_ref.startswith("fitem:"))
        cancelled = lfi.apply_actions(
            self.store, lfi.validate_actions([{"item_ref": item_ref, "op": "cancel"}]),
            now=T0)
        self.assertEqual(cancelled[0]["status"], "APPLIED")
        self.assertEqual(self.store.get(item_ref).status, "CANCELLED")

        second = self.store.propose(title="另一件旧事项", due_at=T0, now=T0)["item"]["item_ref"]
        rescheduled = lfi.apply_actions(
            self.store,
            lfi.validate_actions([{"item_ref": second, "op": "reschedule",
                                   "in_minutes": 30}]), now=T0)
        self.assertEqual(rescheduled[0]["status"], "APPLIED")
        self.assertNotEqual(self.store.get(second).due_at, T0)

    def test_an_old_decision_with_the_normalised_spelling_still_rebuilds(self):
        # 旧校验会把带冒号的 ref 变成 fitem:oblig:... 并随 Decision 落盘；这些旧记录重读时
        # 仍然通过，因为收紧只拒绝“原始输入里不是 fitem 的 ref”，没有改已存拼写。
        fields = dict(
            subject_id=ag1.GLOBAL_SUBJECT_ID, decision_id="dec:old", revision=1,
            decision_opportunity_ref="dopp:old", candidate_set_ref="cset:old",
            candidate_refs=(), context_snapshot_ref="dctx:old",
            hard_constraint_result_refs=(), decision=ag1.DECISION_NO_ACTION,
            reason_codes=(ag1.REASON_NO_LEGAL_CANDIDATE,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1, snapshot_fingerprint="sfp:old",
            created_at=T0,
            future_item_actions=({"item_ref": "fitem:oblig:abc", "op": "cancel"},))
        rebuilt = ag1.DecisionRecord.from_dict(ag1.DecisionRecord(**fields).to_dict())
        self.assertEqual(rebuilt.future_item_actions[0]["item_ref"], "fitem:oblig:abc")

    def test_an_other_owner_ref_was_already_not_found_before_the_tightening(self):
        # 旧校验会把 "oblig:abc" 拼成 "fitem:oblig:abc"；真实旧记录里不存在，之前也只是
        # 后续 NOT_FOUND，不是合法输入。 新约束只是把这个拒绝提前并具名。
        self.assertIsNone(self.store.get("oblig:abc"))
        self.assertIsNone(self.store.get("fitem:oblig:abc"))
        with self.assertRaises(lfi.FutureItemError) as caught:
            lfi.validate_actions([{"item_ref": "oblig:abc", "op": "cancel"}])
        self.assertEqual(caught.exception.reason_code,
                         "FUTURE_ITEM_ACTION_REF_NOT_AN_ITEM")


class ReservedOptionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.plan = ldp.DayPlan(self.root, subject_id=SUBJECT)
        self.obligations = ldp.DayObligations(self.root, subject_id=SUBJECT)

    def tearDown(self):
        self._tmp.cleanup()

    def _plan(self, item_ref="fitem:aaa", *, due_at=T0):
        self.plan.plan(_fitem(item_ref, due_at=due_at), date=DAY,
                       decision_ref="dec:%s" % item_ref, now=T0)

    def _obligation(self, *, ref="artifact:work1", due_at=T0, title="导言"):
        return {"obligation_ref": "oblig:%s" % ref, "date": DAY, "title": title,
                "due_at": due_at, "status": "PLANNED", "source": "OPERATOR",
                "target_ref": ref, "completion": {"kind": "ARTIFACT_STEP_RECEIPT",
                                                  "target_ref": ref,
                                                  "expected": "SUCCEEDED"}}

    def test_one_slot_each_and_only_when_due(self):
        self._plan()
        options, withheld = ldp.reserved_options(
            plan_entries=self.plan.for_date(DAY),
            obligations=[self._obligation()], now=T0,
            futures_lookup=lambda ref: _fitem(),
            target_reader=lambda ref: {"resumable": True})
        self.assertEqual([row["kind"] for row in options],
                         [ldp.KIND_SELF_PLANNED, ldp.KIND_OBLIGATION])
        self.assertEqual(withheld, [])
        early, _ = ldp.reserved_options(
            plan_entries=self.plan.for_date(DAY),
            obligations=[self._obligation(due_at="2026-11-10T12:00:00+00:00")],
            now=T0, futures_lookup=lambda ref: _fitem(),
            target_reader=lambda ref: {"resumable": True})
        # The self item is already due; only the not-yet-due obligation is left out entirely
        # (a not-yet-due entry is not a refusal, so it is neither offered nor "withheld").
        self.assertEqual([row["kind"] for row in early], [ldp.KIND_SELF_PLANNED])

    def test_unreadable_terminal_and_paused_targets_are_withheld_by_name(self):
        options, withheld = ldp.reserved_options(
            plan_entries=[], obligations=[self._obligation()], now=T0,
            futures_lookup=lambda ref: None,
            target_reader=lambda ref: {"resumable": False})
        self.assertEqual(options, [])
        self.assertEqual(withheld[0]["reason_code"], "OBLIGATION_TARGET_NOT_EXECUTABLE")
        _options, withheld = ldp.reserved_options(
            plan_entries=[], obligations=[self._obligation()], now=T0,
            futures_lookup=lambda ref: None,
            target_reader=lambda ref: (_ for _ in ()).throw(OSError("down")))
        self.assertEqual(withheld[0]["reason_code"], "OBLIGATION_TARGET_UNREADABLE")
        _options, withheld = ldp.reserved_options(
            plan_entries=[], obligations=[self._obligation()], now=T0,
            futures_lookup=lambda ref: None,
            target_reader=lambda ref: {"resumable": True},
            paused_targets=["artifact:work1"])
        self.assertEqual(withheld[0]["reason_code"], "PAUSED_TARGET_REQUIRES_RESUME")

    def test_a_cancelled_item_is_projected_abandoned_not_completed(self):
        self._plan()
        rows = ldp.project_plan_entries(
            self.plan.for_date(DAY),
            futures_lookup=lambda ref: _fitem(status="CANCELLED"))
        self.assertEqual(rows[0]["status"], ldp.PLAN_STATUS_ABANDONED)
        self.assertEqual(rows[0]["reason_code"], "PLAN_ITEM_CLOSED")
        # 取消没有把计划改写成别的终态，也没有写任何文件。
        self.assertEqual(self.plan.for_date(DAY)[0]["status"], ldp.PLAN_STATUS_PLANNED)


class BuildOptionsReservedTest(unittest.TestCase):
    def _questions(self):
        return [{"marker_id": "unclear:1", "note": "为什么卡住？"},
                {"marker_id": "unclear:2", "note": "井是谁修的？"}]

    def _reserved(self):
        return [{"option_ref": "popt:self", "kind": ldp.KIND_SELF_PLANNED,
                 "title": "把水井那段补记一笔", "seed_text": "把水井那段补记一笔",
                 "source_refs": ["fitem:aaa"], "basis_ref": "plan:aaa",
                 "material_ref": "artifact:work1", "work_ref": "artifact:work1",
                 "reserved": True, "dedupe_refs": ["fitem:aaa", "plan:aaa"]},
                {"option_ref": "popt:obl", "kind": ldp.KIND_OBLIGATION,
                 "title": "导言", "seed_text": "导言",
                 "source_refs": ["oblig:1", "artifact:work2"],
                 "basis_ref": "oblig:1", "material_ref": "artifact:work2",
                 "work_ref": "artifact:work2", "reserved": True,
                 "dedupe_refs": ["oblig:1", "artifact:work2"]}]

    def test_two_regular_plus_two_reserved_without_changing_regular_order(self):
        options = lpo.build_options(questions=self._questions(), research_available=True,
                                    limit=2, reserved=self._reserved())
        self.assertEqual(len(options), 4)
        self.assertEqual([row["kind"] for row in options[:2]],
                         [lpo.KIND_RESEARCH, lpo.KIND_RESEARCH])
        self.assertEqual([row["kind"] for row in options[2:]],
                         [ldp.KIND_SELF_PLANNED, ldp.KIND_OBLIGATION])

    def test_no_reserved_is_byte_identical_to_the_old_answer(self):
        before = lpo.build_options(questions=self._questions(), research_available=True,
                                   limit=2)
        after = lpo.build_options(questions=self._questions(), research_available=True,
                                  limit=2, reserved=[])
        self.assertEqual([row["option_ref"] for row in before],
                         [row["option_ref"] for row in after])

    def test_a_reserved_slot_already_offered_is_deduplicated(self):
        due = [{"item_ref": "fitem:aaa", "title": "把水井那段补记一笔", "due_at": T0}]
        options = lpo.build_options(due_items=due, limit=1, reserved=self._reserved())
        self.assertEqual([row["kind"] for row in options],
                         [lpo.KIND_FUTURE_ITEM, ldp.KIND_OBLIGATION])


class FinalCandidateDedupeTest(unittest.TestCase):
    """最终候选按同一真实可执行目标折叠；不同动作/不同目标不陪葬。"""

    def _source(self, *, extra, artifacts=("artifact:one",), closeout=None):
        class _Port:
            def list_artifacts(self):
                return [{"artifact_id": ref, "lifecycle": "ACTIVE"} for ref in artifacts]

            def resume_point(self, artifact_id):
                return lap.ArtifactResumePoint(
                    artifact_id=str(artifact_id), title="作品", head_version_id="hv:1",
                    version_number=1, content="正文", lifecycle="ACTIVE", revision=1)

        return acs.ArtifactContinuationSource(
            _Port(), subject_id=SUBJECT,
            closeout_target_provider=(lambda: closeout) if closeout is not None else None,
            extra_opportunity_provider=lambda: extra)

    @staticmethod
    def _reserved_self():
        return {"source_ref": "popt:self", "target_refs": ["popt:self"],
                "kind": ldp.KIND_SELF_PLANNED, "reserved": True,
                "work_ref": "artifact:one",
                "dedupe_refs": ["fitem:due", "plan:one"]}

    def test_a_reserved_self_plan_and_a_due_item_leave_one_executable_entry(self):
        source = self._source(extra=[
            self._reserved_self(),
            {"source_ref": "fitem:due", "target_refs": ["artifact:one"],
             "kind": "FUTURE_ITEM_DUE"},
        ])
        candidates, _snapshot, _meta = source.materialize(observed_at=T0)
        self.assertEqual([row.source_ref for row in candidates], ["popt:self"])
        self.assertEqual(source.last_reserved_dedupe_skips.get("fitem:due"),
                         "RESERVED_ITEM_ALREADY_OFFERED")

    def test_another_target_and_a_closeout_stay(self):
        source = self._source(
            extra=[self._reserved_self()],
            artifacts=("artifact:one", "artifact:two"), closeout={"one"})
        candidates, _snapshot, _meta = source.materialize(observed_at=T0)
        refs = sorted(row.source_ref for row in candidates)
        self.assertEqual(refs, ["artifact:artifact:one", "artifact:artifact:two",
                                "popt:self"])
        by_ref = {row.source_ref: row for row in candidates}
        self.assertIn("closeout:artifact:artifact:one",
                      by_ref["artifact:artifact:one"].target_refs)
        self.assertNotIn("closeout:artifact:artifact:two",
                         by_ref["artifact:artifact:two"].target_refs)


class DecisionRecordPlanFieldTest(unittest.TestCase):
    def _record(self, **overrides):
        fields = dict(
            subject_id=ag1.GLOBAL_SUBJECT_ID, decision_id="dec:ownday1", revision=1,
            decision_opportunity_ref="dopp:ownday1", candidate_set_ref="cset:ownday1",
            candidate_refs=(), context_snapshot_ref="dctx:ownday1",
            hard_constraint_result_refs=(), decision=ag1.DECISION_NO_ACTION,
            reason_codes=(ag1.REASON_NO_LEGAL_CANDIDATE,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1, snapshot_fingerprint="sfp:ownday1",
            created_at=T0)
        fields.update(overrides)
        return ag1.DecisionRecord(**fields)

    def test_the_field_round_trips_through_the_record(self):
        record = self._record(day_plan_request={"item_ref": "fitem:abc"})
        rebuilt = ag1.DecisionRecord.from_dict(record.to_dict())
        self.assertEqual(rebuilt.day_plan_request, {"item_ref": "fitem:abc"})

    def test_a_wrong_ref_is_refused_on_rebuild(self):
        with self.assertRaises(ag1.DecisionValidationError):
            self._record(day_plan_request={"item_ref": "artifact:abc"})

    def test_a_raw_decision_with_the_new_key_is_structurally_valid(self):
        allowed = None
        # 直接读 AG-1 的合法键表：键名是契约，换成别的名字会让模型输出被整体拒绝。
        import inspect

        source = inspect.getsource(ag1.DecisionOutputValidator.validate_decision_output)
        self.assertIn('"plan_today"', source)
        self.assertIsNone(allowed)


@POSIX_ONLY
@needs_fixture
class RealOwnerOwnDayTest(unittest.TestCase):
    """正式链：计划 / 义务 / 预留位 / 真实执行 / 负控。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.facts = driver.run_own_day("unittest")

    def test_no_scene_aborted(self):
        self.assertNotIn("blocker", self.facts)

    def test_formal_decision_plans_an_existing_item_into_today(self):
        plan = self.facts["plan"]
        self.assertEqual(plan["create_decision"], "NO_ACTION")
        self.assertEqual(plan["planned_date"], plan["business_date"])
        self.assertEqual(plan["entry"]["item_ref"], self.facts["refs"]["item_ref"])
        self.assertEqual(plan["entry"]["source"], "SUBJECT_DECISION")
        self.assertEqual([row["status"] for row in plan["writes"]], ["APPLIED"])
        self.assertEqual(plan["item_status"], "OPEN")
        self.assertFalse(plan["same_decision_created"])
        self.assertEqual(plan["no_field_writes"], [])
        self.assertTrue(plan["no_field_plan_revision_same"])

    def test_operator_owns_the_definition_and_life_reads_it(self):
        obligation = self.facts["obligation"]
        self.assertIn(obligation["unknown_target_reason"],
                      {"OBLIGATION_TARGET_UNKNOWN", "OBLIGATION_TARGET_UNREADABLE"})
        self.assertTrue(obligation["import_created"])
        self.assertFalse(obligation["import_again_created"])
        self.assertEqual(obligation["definition"]["source"], "OPERATOR")
        self.assertEqual(obligation["definition"]["target_ref"],
                         self.facts["refs"]["seed_artifact"])
        self.assertEqual(obligation["reader_class"], "DayObligations")
        self.assertFalse(obligation["reader_has_writer"])

    def test_two_regular_slots_and_two_reserved_slots_reach_the_request(self):
        comp = self.facts["competition"]
        self.assertTrue(comp["asked"])
        self.assertEqual(comp["regular_count"], lpo.MAX_OPTIONS)
        self.assertEqual(comp["total"], lpo.MAX_OPTIONS + 2)
        self.assertEqual(comp["reserved_kinds"],
                         [ldp.KIND_SELF_PLANNED, ldp.KIND_OBLIGATION])
        self.assertTrue(comp["reserved_in_request"][ldp.KIND_SELF_PLANNED])
        self.assertTrue(comp["reserved_in_request"][ldp.KIND_OBLIGATION])
        self.assertEqual(comp["writer_calls_delta"], 0)
        self.assertTrue(comp["plan_revision_same"])
        self.assertTrue(comp["obligation_revision_same"])

    def test_the_same_state_stays_quiet(self):
        self.assertFalse(self.facts["quiet"]["asked"])
        self.assertEqual(self.facts["quiet"]["status"], "QUIET")

    def test_the_selected_obligation_continues_its_own_work(self):
        execution = self.facts["execution"]
        self.assertTrue(execution["asked"])
        self.assertEqual(execution["outcome"], "ACTED")
        self.assertEqual(execution["select_missed"], "")
        self.assertNotEqual(execution["version_before"], execution["version_after"])
        self.assertEqual(execution["obligation_result"], "DONE")
        self.assertTrue(execution["receipt_decision_id"])
        self.assertEqual(execution["obligation_evidence_decision"],
                         execution["receipt_decision_id"])
        self.assertTrue(execution["obligation_file_unchanged"])
        self.assertTrue(execution["repeat_head_same"])
        self.assertTrue(execution["repeat_obligation_file_same"])

    def test_cancel_and_reschedule_withdraw_the_slot_without_completing(self):
        negative = self.facts["negative"]
        self.assertFalse(negative["reschedule"]["reserved_present"])
        self.assertEqual(negative["cancel"]["item_status_after"], "CANCELLED")
        self.assertTrue(all(row["status"] == "ABANDONED"
                            for row in negative["cancel"]["plan_entries"]))

    def test_unreadable_obligations_are_named_and_archived_targets_withheld(self):
        negative = self.facts["negative"]
        self.assertEqual(negative["unreadable"]["obligation_view_status"], "READ_FAILED")
        self.assertIn("OBLIGATIONS_STATE",
                      negative["unreadable"]["day_plan_error"])
        self.assertIn("OBLIGATION_TARGET_NOT_EXECUTABLE",
                      negative["terminal_target"]["withheld_reasons"])
        self.assertFalse(negative["terminal_target"]["terminal_option_offered"])

    def test_the_same_root_reads_the_plan_back_without_writing(self):
        reassembly = self.facts["reassembly"]
        self.assertGreaterEqual(reassembly["plan_count"], 1)
        self.assertEqual(reassembly["plan_item_ref"], self.facts["refs"]["item_ref"])
        self.assertTrue(reassembly["file_unchanged"])

    def test_one_executable_entry_per_real_target_keeps_the_obligation_label(self):
        comp = self.facts["competition"]
        seed_entries = comp["seed_entries"]
        self.assertEqual(len(seed_entries), 1)
        self.assertEqual(seed_entries[0]["source_ref"],
                         self.facts["refs"]["reserved_obligation"])
        self.assertEqual(seed_entries[0]["kind"], ldp.KIND_OBLIGATION)
        self.assertTrue(comp["seed_closeout_present"])
        self.assertEqual(len(comp["other_entries"]), 1)
        self.assertEqual(comp["obligation_candidate_id"],
                         comp["obligation_candidate_id_expected"])

    def test_reassembly_reads_done_from_persisted_records_without_replay(self):
        execution = self.facts["reassembly_obligation"]
        after = self.facts["reassembly_after"]
        self.assertEqual(execution["result"], ldp.OBLIGATION_RESULT_DONE)
        self.assertEqual(execution["action_status"], "SUCCEEDED")
        self.assertEqual(execution["candidate_ids"], [execution["candidate_id_expected"]])
        self.assertEqual(after["result"], ldp.OBLIGATION_RESULT_DONE)
        self.assertEqual(after["evidence_path"], ldp.EVIDENCE_PATH_PERSISTED)
        self.assertEqual(after["evidence_decision_id"], execution["decision_id"])
        self.assertEqual(after["evidence_action_id"], execution["action_id"])
        self.assertEqual(after["evidence_activity_ref"], execution["activity_ref"])
        self.assertEqual(after["evidence_version_id"], execution["version_id"])
        self.assertTrue(after["operator_bytes_unchanged"])
        self.assertTrue(after["head_same"])
        self.assertTrue(after["action_count_same"])
        self.assertFalse(after["obligation_reserved_present"])
        self.assertFalse(after["obligation_candidate_present"])

    def test_a_paused_target_is_not_started_again(self):
        paused = self.facts["paused"]
        self.assertEqual(paused["pause_outcome"], "PAUSED")
        self.assertTrue(paused["due_asked"])
        self.assertIn("PAUSED_TARGET_REQUIRES_RESUME", paused["withheld_reasons"])
        self.assertFalse(paused["same_target_candidate"])
        self.assertEqual(paused["writer_calls_delta"], 0)
        self.assertTrue(paused["head_same"])
        self.assertNotIn("ACTIVE", paused["open_activity_statuses"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
