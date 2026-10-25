import sys
sys.path[:0] = ["/mnt/d/nyairo-rc1-body-backend",
                "/mnt/d/nyairo-rc1-body-backend/components/life/scripts"]
import autonomous_life_cycle as alc
import tests.test_v04_autonomous_day_stage as t

A1, A2 = t.A1, t.A2
case = t.StageADayChainTest("test_a_second_work_can_be_continued_instead_of_the_bound_one")
cycle, runtime, port, store, owner = case._cycle("probe", artifacts=(A1, A2))
cycle.work_artifact_id = A1
runtime.candidate_read.rows["cand:current:a2"] = {
    "candidate_id": "cand:current:a2",
    "candidate_kind": "CONTINUE_CURRENT",
    "source_kind": "CURRENT_ACTIVITY",
    "source_ref": "actv:open-on-a2",
    "activity_ref": "actv:open-on-a2",
    "target_refs": [A2, "artifact-version:bbbb"],
}
decision = t._Decision("CONTINUE", selected="cand:current:a2",
                       target_activity_ref="actv:open-on-a2")
point, sel = cycle._read_resume_point()
print("resume_point:", None if point is None else point.artifact_id,
      "selected_by:", sel.get("selected_by"))
step = cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=t.TS0)
print("step artifact:", step["payload_spec"]["artifact_id"],
      "expected_head:", step["payload_spec"]["expected_head"])
print("A2:", A2, "A1:", A1)
