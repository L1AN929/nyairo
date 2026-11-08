"""combined-natural-A 四个聚合 case；scratch 保留，不运行正式入口、不联网。"""
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import combined_observation_a as combined
import fast_day
import idle2_bounded_run as idle
from life_request_budget import FAILSTOP_FILENAME

SECRET = "PRIVATE_SENTINEL prompt completion reasoning title endpoint"


def scratch():
    return Path(tempfile.mkdtemp(prefix="combined-a-case-"))


def future_identity(root):
    now = datetime.now(timezone.utc)
    return replace(combined.BATCH_A, root=root / "instance",
                   authority=root / "authority/authority.db",
                   latest_start=now + timedelta(days=1),
                   deadline=now + timedelta(days=2))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class CombinedObservationATests(unittest.TestCase):
    def test_01_identity_profile_and_idle2_defaults_do_not_drift(self):
        profile = combined.BATCH_A.profile()
        self.assertEqual(profile["run_id"], "run:combined-natural-20261106-a")
        self.assertEqual(profile["domain_id"], "fastday:combined-natural-20261106-a")
        self.assertEqual((profile["historical_requests"], profile["new_cap"],
                          profile["utc_day_cap"], profile["cumulative_total"],
                          profile["worst_case_cumulative"]), (522, 20, 300, 1000, 542))
        self.assertEqual((profile["real_seconds"], profile["logical_seconds"],
                          profile["rate"], profile["cleanup_seconds"]), (7200, 86400, 12, 60))
        self.assertEqual(profile["logical_anchor"], "2026-11-06T08:00:00+08:00")
        self.assertEqual(profile["latest_start"], "2026-11-06T22:30:00+08:00")
        self.assertEqual(profile["request_deadline"], "2026-11-07T00:50:00+08:00")
        # 原 idle2 默认一个值都不许漂移。
        old = idle.PROFILE
        self.assertEqual((old["run_id"], old["domain_id"]), (idle.RUN_ID, idle.DOMAIN))
        self.assertEqual((old["historical_requests"], old["new_cap"],
                          old["worst_case_cumulative"]), (490, 20, 510))
        for key in ("utc_day_cap", "cumulative_total", "real_seconds",
                    "logical_seconds", "rate", "cleanup_seconds"):
            self.assertEqual(old[key], profile[key], key)
        self.assertNotEqual(combined.BATCH_A.root, idle.IDLE2.root)
        self.assertNotEqual(combined.BATCH_A.authority, idle.IDLE2.authority)

    def test_02_scratch_profile_prepare_then_pure_read_zero_ledger(self):
        root = scratch()
        ident = future_identity(root)
        prepared = idle.prepare(identity=ident)
        self.assertTrue(prepared["ok"], prepared)
        _mode, resolved_state = idle._identity(root / "instance")
        state = Path(resolved_state)
        mode = idle.read(fast_day.mode_path(root / "instance"))
        self.assertEqual((mode["historical_requests"], mode["max_new_requests"],
                          mode["request_total_limit"], mode["rate"],
                          mode["logical_seconds"]), (522, 20, 1000, 12, 86400))
        self.assertEqual(mode["request_deadline"], ident.deadline.isoformat())
        self.assertEqual(mode["run_id"], "combined-natural-20261106-a")
        env = idle.read(root / "instance/one-day/instance.json")["environment"]
        self.assertEqual(env["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"], "300")
        self.assertEqual(env["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"], "1000")
        self.assertEqual(env["NYA02_BOUNDED_FAILSTOP"], "1")
        caps = prepared["actual_caps"]
        self.assertEqual((caps["history"], caps["new_cap"], caps["utc_day_cap"],
                          caps["cumulative_total"], caps["reserved"], caps["utc_used"]),
                         (522, 20, 300, 1000, 0, 0))
        self.assertFalse(prepared["started"])

        before = snapshot(root / "instance")
        view = idle.status(root / "instance", identity=ident)
        combined_view = combined.combined_observation(root / "instance", state, identity=ident)
        self.assertTrue(view["ok"], view)
        self.assertEqual(combined_view["profile"], ident.profile())
        self.assertEqual(view["actual_caps"]["reserved"], 0)
        self.assertEqual(view["actual_caps"]["utc_used"], 0)
        self.assertEqual(before, snapshot(root / "instance"))

        repeated = idle.prepare(identity=ident)
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["reason_code"], "FORMAL_ROOT_NOT_FRESH")

    def test_03_original_guards_refuse_without_running_anything(self):
        root = scratch()
        ident = future_identity(root)
        absent = idle.status(root / "absent", identity=ident)
        self.assertFalse(absent["ok"])
        self.assertEqual(absent["reason_code"], "IDLE2_NOT_PREPARED")
        self.assertFalse((root / "absent").exists())

        past = replace(ident, root=root / "past",
                       authority=root / "past-authority/authority.db",
                       latest_start=datetime.now(timezone.utc) - timedelta(minutes=1))
        self.assertEqual(idle.prepare(identity=past)["reason_code"], "LATEST_START_PASSED")
        self.assertFalse((root / "past").exists())

        occupied = root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        refused = idle.prepare(identity=replace(
            ident, root=occupied, authority=root / "occupied-authority/authority.db"))
        self.assertEqual(refused["reason_code"], "FORMAL_ROOT_NOT_FRESH")
        self.assertFalse((occupied / "mode.json").exists())

        configured = idle.configure(root / "instance", root / "authority/authority.db",
                                    provision=False, identity=ident)
        self.assertTrue(configured["ok"], configured)
        state = Path(configured["state_root"])
        self.assertEqual(idle.prepare(identity=ident)["reason_code"], "FORMAL_ROOT_NOT_FRESH")

        write_json(fast_day.run_path(state), {"started_utc": "2026-11-06T09:00:00+00:00"})
        view = idle.status(root / "instance", identity=ident)
        self.assertTrue(view["started"])
        self.assertEqual(idle.run(identity=ident)["reason_code"], "IDLE2_RUN_REFUSED")
        self.assertFalse(view["failure_locked"])

        write_json(state / FAILSTOP_FILENAME,
                   {"failure_category": "PROVIDER_OR_TRANSPORT_FAILURE"})
        view = idle.status(root / "instance", identity=ident)
        self.assertTrue(view["failure_locked"])
        self.assertEqual(view["failure_category"], "PROVIDER_OR_TRANSPORT_FAILURE")
        self.assertEqual(idle.run(identity=ident)["reason_code"], "IDLE2_RUN_REFUSED")

        wrong_deadline = replace(ident, deadline=ident.deadline + timedelta(seconds=1))
        self.assertEqual(idle.status(root / "instance", identity=wrong_deadline)["reason_code"],
                         "IDENTITY_MISMATCH")

    def test_04_combined_projection_join_unknown_and_privacy(self):
        root = scratch()
        state = root / "state"
        world = root / "world-home"
        act_go = "actv_0123456789abcdef"
        act_other = "actv_fedcba9876543210"
        act_go2 = "actv_1111111111111111"
        write_json(root / "one-day/instance.json", {"world_home": str(world)})
        (state / "candidate_audit_journal.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (state / "candidate_audit_journal.jsonl").write_text("\n".join(json.dumps(row) for row in (
            {"candidate_id": "cand:move", "source_ref": "wopp:move:street_1:r1", "title": SECRET},
            {"candidate_id": "cand:stay",
             "source_ref": "wopp:stay:home:r1:DAZE", "title": SECRET})) + "\n", encoding="utf-8")
        # snapshot 故意不含 dec:one；它只经 AG1 已提交 journal 的 map_delta 可见。
        write_json(state / "agency_decision_state.json", {
            "candidate_count": 99,
            "accessibility_views_by_id": {"acc:one": {"candidate_entries": {
                "cand:move": {}, "cand:stay": {}}}},
            "decisions_by_id": {
                "dec:two": {"decision": "NO_ACTION", "decision_route": "COGNITIVE_REQUIRED",
                            "reason_codes": ["NO_ACTION_CHOSEN"]},
                "dec:three": {"decision": "NO_ACTION", "decision_route": "RULE_RESOLVED",
                              "reason_codes": ["NO_LEGAL_CANDIDATE"]}}})
        (state / "agency_decision_journal.jsonl").write_text(json.dumps({
            "state_delta": {"map_updates": {"decisions_by_id": {"dec:one": {
                "decision": "CONTINUE", "decision_route": "COGNITIVE_REQUIRED",
                "reason_codes": [], "selected_candidate_ref": "cand:move",
                "short_rationale": SECRET}}}}}) + "\n", encoding="utf-8")
        write_json(state / "canonical_life_state.json", {"activities": {act_go: {
            "activity_kind": "GO_TO_OBSERVED_PLACE", "decision_ref": "dec:one",
            "status": "ACTIVE", "revision": 5, "completion_evidence_ref": None,
            "title": SECRET}}})
        # 正式桥：Activity -> AG2 integration.action_refs -> AR-0 Action.idempotency_key
        # -> world_journey.active.request_key。另一个 Activity/Action 故意 revision 更高。
        write_json(state / "life_integration_state.json", {"integrations_by_id": {
            "lint:one": {"integration_id": "lint:one", "activity_ref": act_go,
                         "action_refs": ["actn:move"]},
            "lint:other": {"integration_id": "lint:other", "activity_ref": act_other,
                           "action_refs": ["actn:other"]}}})
        write_json(state / "action_reality_state.json", {"actions": {
            "actn:move": {"action_id": "actn:move", "activity_ref": act_go,
                          "idempotency_key": "ar0_act:step-one", "status": "SUCCEEDED",
                          "revision": 3, "target_ref": "street_1"},
            "actn:other": {"action_id": "actn:other", "activity_ref": act_other,
                           "idempotency_key": "ar0_act:step-other", "status": "SUCCEEDED",
                           "revision": 9, "target_ref": "street_1"}}})
        wake_go = {"kind": "CHECK_WINDOW", "status": "OK", "status_class": "ACTED",
                   "activity_ref": act_go, "decision": "CONTINUE",
                   "outcome": "NO_STEP_AVAILABLE", "llm_calls": 1, "detail": SECRET,
                   "extra": {"decision_id": "dec:one",
                             # 与正式记录不同，读面必须用已提交记录覆盖这两个诱饵。
                             "decision_route": "RULE_RESOLVED",
                             "selected_candidate_ref": "cand:stay",
                             "owner_version_created": False, "owner_version_id": SECRET,
                             "work_no_change": {"reason_code": "NO_CHANGE",
                                                "owner_called": False,
                                                "version_created": False},
                             "step_readiness": {"available": False,
                                                "reason_code": "WORLD_MOVE_NO_FURTHER_STEP",
                                                "activity_ref": act_go,
                                                "target_ref": "place:street_1",
                                                "detail": SECRET}}}
        # 最后一条 wake 是另一个 Activity，且带更高 revision 的 Action 与顶层 decision_id 诱饵。
        wake_other = {"kind": "CHECK_WINDOW", "status": "OK", "status_class": "ACTED",
                      "activity_ref": act_other, "decision": "CONTINUE",
                      "decision_id": "dec:three", "action_id": "actn:other",
                      "action_status": "SUCCEEDED", "outcome": "ACTED",
                      "llm_calls": 1, "detail": SECRET,
                      "extra": {"decision_id": "dec:two",
                                "decision_route": "RULE_RESOLVED",
                                "selected_candidate_ref": "cand:stay",
                                "owner_version_created": False}}
        (state / "autonomous_wakes.jsonl").write_text(
            "\n".join(json.dumps(row) for row in (wake_go, wake_other)) + "\n",
            encoding="utf-8")
        (state / "resident_journal.jsonl").write_text(json.dumps({"due": {"extra": {
            "world_reading": {"state": "APPLIED", "item_type": "memo",
                              "version_number": 3, "private_note": SECRET}}}}) + "\n",
            encoding="utf-8")
        # 真实 actv_ Activity id 要让 safe_observation 看见，而不是被旧正则压成 UNKNOWN。
        write_json(state / "activity_progress_state.json", {"consumed_evidences_by_id": {
            "cprog:one": {"evidence": {"activity_id": act_go,
                                       "authority_domain": "world_authority",
                                       "observed_facts": {"page": 4}},
                          "disposition": "CONSUMED", "bound_activity_id": act_go}}})
        journey = {"schema_version": 1, "revision": 2, "history": [], "active": {
            "journey_id": "journey:one", "world_id": "world:one",
            "graph_fingerprint": "fp", "route_revision": "route:1",
            "origin_location_id": "home", "target_location_id": "street_1",
            "route": ["street_1"], "current_segment": 0, "status": "completed",
            "request_key": "ar0_act:step-one", "request_digest": "digest",
            "created_at": "2026-11-06T00:00:00Z", "updated_at": "2026-11-06T00:18:34Z",
            "error": None,
            "segments": [{"index": 0, "destination_location_id": "street_1",
                          "execution_id": "exec:one", "travel_id": "travel:one",
                          "status": "confirmed", "started_at": "2026-11-06T00:00:00Z",
                          "expected_arrival_at": "2026-11-06T00:18:00Z",
                          "confirmed_at": "2026-11-06T00:18:34Z"}]}}
        write_json(world / "data/world/world_journey.json", journey)
        write_json(world / "data/world/world_travel_delivery.json", {"travels": [
            {"execution_id": "exec:one", "status": "ARRIVED",
             "destination_location_id": "street_1", "arrived_at": "2026-11-06T00:18:34Z"},
            {"execution_id": "exec:other", "status": "ARRIVED",
             "destination_location_id": "street_1", "arrived_at": "2026-11-06T00:20:00Z"}]})
        write_json(world / "data/world/world_state.json",
                   {"location": {"place_id": "street_1", "area_id": "street"}})

        view = combined.combined_observation(root, state)
        go = view["go_to"]
        self.assertEqual(go["activity_ref"], act_go)
        self.assertEqual((go["decision"], go["decision_route"],
                          go["selected_candidate_ref"]),
                         ("CONTINUE", "COGNITIVE_REQUIRED", "cand:move"))
        self.assertIs(go["adopted"], True)
        self.assertEqual((go["step_available"], go["step_reason_code"],
                          go["step_target_ref"]),
                         (False, "WORLD_MOVE_NO_FURTHER_STEP", "place:street_1"))
        self.assertEqual((go["action_ref"], go["action_status"], go["action_source"]),
                         ("actn:move", "SUCCEEDED", "ACTION_STORE"))
        self.assertEqual(go["activity_journey_join"], "SAME_ACTIVITY")
        self.assertEqual((go["journey_status"], go["segment_execution_id"]),
                         ("completed", "exec:one"))
        self.assertEqual((go["travel_join"], go["travel_status"]),
                         ("SAME_EXECUTION", "ARRIVED"))
        self.assertIs(go["arrived"], True)
        self.assertIs(go["activity_completed"], False)
        self.assertEqual(go["world_location_ref"], "street_1")
        self.assertIs(go["location_matches_journey_target"], True)
        self.assertEqual(go["same_activity_continue_no_step"], 1)
        self.assertEqual((go["scope"], go["candidate_count"]),
                         ("LATEST_REVISION_GO_TO_ONLY", 1))
        self.assertIs(view["world_journey"]["arrived"], True)
        self.assertEqual(view["world_journey"]["segment_execution_id"], "exec:one")
        records = view["decision"]["records"]
        self.assertEqual((records["model_no_action_chosen"],
                          records["rule_no_legal_candidate"],
                          records["continue_with_work_no_change"]), (1, 1, 1))
        # decision_id 只认 extra；顶层 dec:three 是诱饵。route/selected 只认已提交记录。
        last = view["decision"]["last_wake"]
        self.assertEqual((last["decision"], last["decision_id"], last["decision_route"],
                          last["selected_candidate_ref"], last["owner_version_created"]),
                         ("CONTINUE", "dec:two", "COGNITIVE_REQUIRED", "UNKNOWN", False))
        self.assertIs(last["committed_record_present"], True)
        self.assertEqual(view["outcome"]["last_wake"], "ACTED")
        self.assertEqual(view["outcome"]["work_no_change"], "UNKNOWN")
        self.assertEqual(view["idle"]["DAZE"]["offered_candidates"], 1)
        self.assertEqual(view["materialization_evidence"], "PRESENT")
        self.assertTrue(any(event.get("world_reading_state") == "APPLIED"
                            and event.get("owner_version_number") == 3
                            for event in view["reading"]["events"]))
        self.assertTrue(any(event.get("activity_id") == act_go
                            and event.get("page") == 4
                            for event in view["reading"]["events"]))
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(view))

        # 另一个 GO_TO Activity revision 更高、目标相同、没有任何桥：不能把它的到达借过来。
        write_json(state / "canonical_life_state.json", {"activities": {
            act_go: {"activity_kind": "GO_TO_OBSERVED_PLACE", "decision_ref": "dec:one",
                     "status": "ACTIVE", "revision": 5, "completion_evidence_ref": None,
                     "title": SECRET},
            act_go2: {"activity_kind": "GO_TO_OBSERVED_PLACE", "decision_ref": "dec:one",
                      "status": "ACTIVE", "revision": 99, "completion_evidence_ref": None,
                      "title": SECRET}}})
        view = combined.combined_observation(root, state)
        go = view["go_to"]
        self.assertEqual(go["activity_ref"], act_go2)
        self.assertEqual(go["activity_journey_join"], "NO_INTEGRATION_RECORD")
        self.assertEqual(go["arrived"], "UNKNOWN")
        self.assertEqual(go["journey_ref"], "UNKNOWN")
        # 最后一条 wake 属于 act_other 且动作 revision 更高，仍不能贴到这个 Activity。
        self.assertEqual(go["action_ref"], "UNKNOWN")
        self.assertEqual(go["candidate_count"], 2)
        self.assertIs(view["world_journey"]["arrived"], True)

        # 另一个 Action 的旅程同地、同 execution ARRIVED：世界旅程自身为真，但不属于 act_go。
        write_json(state / "canonical_life_state.json", {"activities": {act_go: {
            "activity_kind": "GO_TO_OBSERVED_PLACE", "decision_ref": "dec:one",
            "status": "ACTIVE", "revision": 5, "completion_evidence_ref": None,
            "title": SECRET}}})
        journey["active"]["request_key"] = "ar0_act:step-other"
        write_json(world / "data/world/world_journey.json", journey)
        view = combined.combined_observation(root, state)
        go = view["go_to"]
        self.assertEqual(go["activity_ref"], act_go)
        self.assertEqual(go["activity_journey_join"], "JOURNEY_REQUEST_KEY_MISMATCH")
        self.assertEqual(go["arrived"], "UNKNOWN")
        self.assertEqual(go["journey_ref"], "UNKNOWN")
        self.assertEqual(go["action_ref"], "actn:move")
        self.assertIs(view["world_journey"]["arrived"], True)

        # 只有别的 execution 在同一目的地 ARRIVED：不能混成这次到达。
        journey["active"]["request_key"] = "ar0_act:step-one"
        write_json(world / "data/world/world_journey.json", journey)
        write_json(world / "data/world/world_travel_delivery.json", {"travels": [
            {"execution_id": "exec:other", "status": "ARRIVED",
             "destination_location_id": "street_1", "arrived_at": "2026-11-06T00:20:00Z"}]})
        view = combined.combined_observation(root, state)
        self.assertEqual(view["go_to"]["activity_journey_join"], "SAME_ACTIVITY")
        self.assertEqual(view["go_to"]["travel_join"], "NO_MATCHING_EXECUTION")
        self.assertEqual(view["go_to"]["travel_status"], "UNKNOWN")
        self.assertEqual(view["go_to"]["arrived"], "UNKNOWN")
        self.assertEqual(view["world_journey"]["arrived"], "UNKNOWN")
        self.assertEqual(view["go_to"]["world_location_ref"], "street_1")

        # Activity 完成与到达各报各的：位置相同、完成证据存在，仍不能把缺失的 travel 补成到达。
        write_json(state / "canonical_life_state.json", {"activities": {act_go: {
            "activity_kind": "GO_TO_OBSERVED_PLACE", "decision_ref": "dec:one",
            "status": "COMPLETED", "revision": 6,
            "completion_evidence_ref": "pevid:go", "title": SECRET}}})
        view = combined.combined_observation(root, state)
        self.assertIs(view["go_to"]["activity_completed"], True)
        self.assertEqual(view["go_to"]["completion_evidence_ref"], "pevid:go")
        self.assertEqual(view["go_to"]["arrived"], "UNKNOWN")
        write_json(world / "data/world/world_travel_delivery.json", {"travels": [
            {"execution_id": "exec:one", "status": "ARRIVED",
             "destination_location_id": "street_1", "arrived_at": "2026-11-06T00:18:34Z"}]})
        view = combined.combined_observation(root, state)
        self.assertIs(view["go_to"]["activity_completed"], True)
        self.assertIs(view["go_to"]["arrived"], True)

        # 未绑定同一 activity 的 readiness 不能借来当当前 step；缺映射一律 UNKNOWN。
        (state / "autonomous_wakes.jsonl").write_text(json.dumps({
            "activity_ref": act_go, "decision": "CONTINUE",
            "outcome": "NO_STEP_AVAILABLE", "extra": {"step_readiness": {
                "available": True, "reason_code": "WORLD_STEP_AVAILABLE"}}}) + "\n",
            encoding="utf-8")
        view = combined.combined_observation(root, state)
        self.assertEqual(view["go_to"]["step_available"], "UNKNOWN")
        self.assertEqual(view["go_to"]["step_reason_code"], "UNKNOWN")
        self.assertEqual(view["go_to"]["same_activity_continue_no_step"], 1)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(view))

        absent = root / "absent-root"
        blank = combined.combined_observation(absent, None)
        self.assertEqual(blank["go_to"]["arrived"], "UNKNOWN")
        self.assertEqual(blank["go_to"]["activity_ref"], "UNKNOWN")
        self.assertEqual(blank["go_to"]["activity_journey_join"], "UNKNOWN")
        self.assertEqual(blank["go_to"]["candidate_count"], "UNKNOWN")
        self.assertEqual(blank["world_journey"]["arrived"], "UNKNOWN")
        self.assertEqual(blank["decision"]["records"]["model_no_action_chosen"], "UNKNOWN")
        self.assertEqual(blank["outcome"]["last_wake"], "UNKNOWN")
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
