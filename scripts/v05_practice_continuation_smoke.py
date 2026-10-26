#!/usr/bin/env python3
"""零模型真实 owner 冒烟：同一作品的正式结果 → 有证据评价 → 同一作品续作 → 退休损失。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自己的
   Unix socket，落盘）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、B 的实践工作台、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、
   AR-0 受管作品适配器。决策与内容回答是脚本替身（文件里明示），不宣称真实模型自然采用。
2. 第一轮：同一个指定作品 A 得到真实 owner 版本，AG-2 让结果成为可回忆经历；同一轮的
   正式 Decision 原始 JSON 带 ``method_proposal``，经 AG-1 校验、DecisionRecord 提交、
   cycle 自己的 ``_save_method_proposal`` 由真实 owner 存成 TENTATIVE。
3. 另建一件可续作品 B，并把本轮绑定改到 B：后续每一轮都断言 step 仍写 A（候选自带的
   owner 目标优先，不串目标），B 的版本不变。
4. 第二轮：正式 Decision ``CONTINUE`` A 并命名该做法（AG-1 用本轮自己的允许清单接受）；
   真实 writer 请求（正式 constructed request）真的带着该做法的 instruction/scope/
   uncertainty，真实 owner 写 A 的下一版；usage 的后果 refs 来自 owner receipt。
5. 第三轮：用 A 的可读经历作证据，正式 Decision 命名同一做法并给出 ``HELPED``；owner
   把它推进 TRIAL。第四轮的决策视图（本轮真实请求的 situation_context）读到 TRIAL 与
   有界评价投影（HELPED / 被评价版本 / 证据条数），并继续 A。
6. 第五轮：同一套正式阶段给出 ``NOT_HELPED`` 反例，状态 CONTESTED、反例计数 1；
   第六轮的决策视图读到 CONTESTED 与 NOT_HELPED；做法仍可命名（反例是状态不是损失）。
7. 损失：沿 owner 既有 ``retire_method`` 写一个 RETIRED 版本（CAS，不删文档）。下一正式
   回合仍命名旧手柄时，AG-1 用现读的允许清单拒绝（``INVENTED_METHOD_REF``，无 Decision
   提交）；同一状态下一次不引用旧手柄的合法 ``CONTINUE`` 仍写 A，writer 请求不再携带
   退休做法的指令；档案、版本与正文仍可读。
8. 所有自有 runtime / 服务在 finally 关闭、lease 释放；夹具根保留便于复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_practice_continuation_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

TS = "2026-11-10T09:00:00+00:00"
CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-64s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def bare(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def work_ref(value) -> str:
    text = bare(value)
    return "artifact:%s" % text if text else ""


def version(port, artifact_id) -> int:
    point = port.resume_point(artifact_id)
    return int(getattr(point, "version_number", 0) or 0)


def continue_candidate(chain, artifact_id: str):
    """The exact ``CONTINUE_CURRENT`` candidate whose owner target is this artifact.

    Whole-ref matching only: the closeout wrapper carries the artifact id inside its own
    namespace, and a substring match would pick it.  Non-current sources are ignored.
    """

    import past_candidate_bridge as pcb

    wanted = bare(artifact_id)
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=TS).candidate_refs
    matches = []
    for candidate_ref in candidates:
        row = chain.runtime.candidate_read.get_candidate(str(candidate_ref)) or {}
        if str(row.get("candidate_kind") or "") != "CONTINUE_CURRENT":
            continue
        if str(row.get("source_kind") or "") != "CURRENT_ACTIVITY":
            continue
        target = pcb.work_ref_from_candidate(row, exclusions=chain.cycle._non_work_artifact_ids())
        if target and bare(target) == wanted:
            matches.append(str(candidate_ref))
    return matches[0] if len(matches) == 1 else None


def prompt_practice(chain) -> dict:
    """The practice block of the *last real* writer request, or ``{}``."""

    if not chain.client.prompts:
        return {}
    try:
        payload = json.loads(chain.client.prompts[-1]["user"])
    except Exception:
        return {}
    value = payload.get("practice_context")
    return dict(value) if isinstance(value, dict) else {}


def install_decision_view_recorder(chain) -> list:
    """Record the practice slice of each *real* decision request, in order.

    The adapter calls ``cognition.invoke`` for every formal round; recording the request's
    ``situation_context.practice`` is the same data the model would have seen, not a re-read
    after the fact (the cycle enriches practice once per round).
    """

    views: list[dict] = []
    original = chain.cognition.invoke

    def invoke(request, *, schema_retry_index: int = 0):
        situation = getattr(request, "situation_context", {}) or {}
        views.append(dict((situation.get("practice") or {})))
        return original(request, schema_retry_index=schema_retry_index)

    chain.cognition.invoke = invoke
    return views


def run(chain, *, decision: str, candidate, fields: dict, trigger: str):
    """Drive one real ``evaluate_once``; return (round report, this round's decision view)."""

    before = len(chain.decision_views)
    chain.cognition.script = dict(
        {"decision": decision, "selected_candidate_id": candidate,
         "deferred_candidate_ids": []}, **fields)
    out = chain.cycle.evaluate_once(trigger_ref=trigger, act=True)
    view = dict(chain.decision_views[before]) if len(chain.decision_views) > before else {}
    return out, view


def offered_row(view: dict, method_ref: str):
    rows = [row for row in (view.get("methods") or []) if row.get("method_ref") == method_ref]
    return rows[0] if len(rows) == 1 else None


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import agency_decision_ag1 as ag1
    import result_method_behavior_smoke as harness

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("v05-continuation")
    chain.decision_views = install_decision_view_recorder(chain)
    try:
        # -- round 1: same work A gets a real owner version + the Decision's own proposal ----
        seed = harness.seed_method_after_first_write(chain)
        seed_checks = list(harness._CHECKS)
        method_id = str(seed.method_id)
        method_ref = str(seed.method_ref)
        first_receipt = seed.round_out.get("step_receipt") or {}
        artifact_a = str(first_receipt.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        a_after_first = version(chain.port, artifact_a)
        check("round 1 wrote the same work A through the real owner",
              bool(artifact_a) and a_after_first >= 2,
              {"artifact": artifact_a, "version": a_after_first})
        check("round 1 produced a readable experience for future evidence",
              bool(seed.experience_id), {"experience": seed.experience_id})

        # -- another continuable work B; the round is now bound to *it* --------------------
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件可续作品",
            content="另一件正文第一句。\n", grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="v05-continuation:create-b")
        check("the other work B was really created", bool(getattr(created_b, "applied", True)),
              getattr(created_b, "to_dict", lambda: {})())
        b_row = next((row for row in chain.port.list_artifacts()
                      if str(row.get("title") or "") == "另一件可续作品"), None)
        artifact_b = str((b_row or {}).get("artifact_id") or "")
        check("B is readable from the owner list", bool(artifact_b), {"artifact": artifact_b})
        b_before = version(chain.port, artifact_b)
        chain.cycle.work_artifact_id = artifact_b
        check("the round's own binding is the other work B (A must still win)",
              bare(chain.cycle.work_artifact_id) == bare(artifact_b))

        # -- round 2: CONTINUE A, name the method, no verdict yet; real A version ----------
        candidate = continue_candidate(chain, artifact_a)
        check("round 2 offers exactly one exact CONTINUE candidate for A",
              bool(candidate), {"candidate": candidate, "artifact": artifact_a})
        chain.client.payload = {"kind": "append_paragraph", "text": "第二段：先写结论再补理由。",
                                "self_note": "stub: write A v2"}
        out2, _view2 = run(chain, decision="CONTINUE", candidate=candidate,
                           fields={"method_ref": method_ref,
                                   "short_rationale": "stub: follow the method"},
                           trigger="evt:v05-continuation:2")
        a_after_second = version(chain.port, artifact_a)
        usage2 = out2.get("method_usage") or {}
        prompt2 = prompt_practice(chain)
        methods2 = prompt2.get("methods") or []
        check("round 2 acted through the formal chain", out2.get("outcome") == "ACTED",
              {key: out2.get(key) for key in ("outcome", "detail", "decision", "practice_error")})
        check("round 2 wrote A (not the bound work B)",
              a_after_second == a_after_first + 1
              and version(chain.port, artifact_b) == b_before,
              {"a_before": a_after_first, "a_after": a_after_second,
               "b_before": b_before, "b_after": version(chain.port, artifact_b)})
        check("round 2's step target is A, not B",
              bare(out2.get("step_work_target") or "") == bare(artifact_a)
              or bare((out2.get("step_receipt") or {}).get("artifact_id")) == bare(artifact_a),
              {"step_work_target": out2.get("step_work_target"),
               "receipt": bare((out2.get("step_receipt") or {}).get("artifact_id"))})
        check("round 2 binds the offered method/version",
              bare(usage2.get("artifact_id")) == bare(method_id)
              and bool(usage2.get("version_ref")), usage2)
        check("round 2's consequence refs come from the owner receipt",
              work_ref(artifact_a) in (usage2.get("consequence_refs") or [])
              and any(str(ref).startswith("artifactversion:") for ref in
                      (usage2.get("consequence_refs") or [])), usage2)
        check("round 2's formal writer request carries the method's instruction",
              [row.get("method_ref") for row in methods2] == [method_ref]
              and methods2[0].get("instruction") == harness.METHOD_INSTRUCTION
              and methods2[0].get("scope") == harness.METHOD_SCOPE
              and methods2[0].get("uncertainty") == harness.METHOD_UNCERTAINTY,
              {"methods": len(methods2), "has_instruction": bool(methods2),
               "matched": bool(methods2) and methods2[0].get("instruction")
               == harness.METHOD_INSTRUCTION})
        handoff2 = out2.get("experience_handoff") or {}
        experiences = chain.cycle.practice.experiences()
        seed_handoff = seed.round_out.get("experience_handoff") or {}
        check("round 2 got its own result-level handoff for the second version",
              str(handoff2.get("handoff_id") or "").startswith("lexp:result:")
              and handoff2.get("handoff_id") != seed_handoff.get("handoff_id")
              and str(handoff2.get("disposition") or "") == "ACCEPT"
              and len(experiences) >= 2,
              {"handoff": handoff2.get("handoff_id"),
               "round1_handoff": seed_handoff.get("handoff_id"),
               "disposition": handoff2.get("disposition"),
               "experiences": len(experiences)})
        check("the evidence used for the verdict is still readable from A's result",
              bool(chain.cycle.practice.read_experience(seed.experience_id)),
              {"experience": seed.experience_id})

        # -- round 3: formal HELPED verdict; TRIAL ----------------------------------------
        candidate = continue_candidate(chain, artifact_a)
        chain.client.payload = {"kind": "append_paragraph", "text": "第三段：结论先行确实顺。",
                                "self_note": "stub: write A v3"}
        out3, _view3 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "这次先写结论，确实顺一些",
                             "experience_ids": [str(seed.experience_id)]},
            "short_rationale": "stub: verdict"}, trigger="evt:v05-continuation:3")
        a_after_third = version(chain.port, artifact_a)
        trial3 = out3.get("method_trial") or {}
        usage3 = out3.get("method_usage") or {}
        stored3 = chain.cycle.practice.read_method(method_id)
        check("round 3's formal verdict is applied by the real owner",
              trial3.get("outcome") == "HELPED" and trial3.get("method_state") == "TRIAL"
              and trial3.get("evidence_usable") is True,
              {key: trial3.get(key) for key in ("outcome", "method_state", "evidence_usable")})
        check("round 3 also continues A and leaves B alone",
              a_after_third == a_after_second + 1 and version(chain.port, artifact_b) == b_before,
              {"a": a_after_third, "b": version(chain.port, artifact_b)})
        check("the recorded verdict names the offered version, not the method document",
              len(stored3.get("trials") or []) == 1
              and stored3["trials"][0].get("version_id") == usage3.get("version_ref")
              and bare(usage3.get("artifact_id")) == bare(method_id),
              {"trials": len(stored3.get("trials") or []),
               "trial_version": (stored3.get("trials") or [{}])[0].get("version_id"),
               "usage_version": usage3.get("version_ref")})
        check("the consequence refs are A's real version, separate from the method head",
              work_ref(artifact_a) in (usage3.get("consequence_refs") or [])
              and bare(method_id) not in (usage3.get("consequence_refs") or []), usage3)

        # -- round 4: the *next* decision request really sees TRIAL + the verdict ----------
        candidate = continue_candidate(chain, artifact_a)
        chain.client.payload = {"kind": "append_paragraph", "text": "第四段：接着用这个方法。",
                                "self_note": "stub: write A v4"}
        out4, view4 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref, "short_rationale": "stub: follow again"},
            trigger="evt:v05-continuation:4")
        row4 = offered_row(view4, method_ref)
        check("round 4's decision request reads TRIAL and the explicit verdict",
              out4.get("outcome") == "ACTED" and bool(row4)
              and row4.get("method_state") == "TRIAL"
              and (row4.get("evaluation") or {}).get("outcome") == "HELPED"
              and (row4.get("evaluation") or {}).get("version_id") == usage3.get("version_ref")
              and (row4.get("evaluation") or {}).get("evidence_count") == 1,
              {"state": (row4 or {}).get("method_state"),
               "evaluation": (row4 or {}).get("evaluation")})
        a_after_fourth = version(chain.port, artifact_a)
        check("round 4 still continues A and leaves B alone",
              a_after_fourth == a_after_third + 1
              and version(chain.port, artifact_b) == b_before,
              {"a": a_after_fourth, "b": version(chain.port, artifact_b)})

        # -- round 5: a formal counterexample -> CONTESTED --------------------------------
        candidate = continue_candidate(chain, artifact_a)
        chain.client.payload = {"kind": "append_paragraph", "text": "第五段：换个写法试试。",
                                "self_note": "stub: write A v5"}
        out5, _view5 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "NOT_HELPED",
                             "detail": "这次先写结论反而卡住",
                             "experience_ids": [str(seed.experience_id)]},
            "short_rationale": "stub: counterexample"}, trigger="evt:v05-continuation:5")
        trial5 = out5.get("method_trial") or {}
        check("round 5's formal counterexample is CONTESTED",
              trial5.get("outcome") == "NOT_HELPED"
              and trial5.get("method_state") == "CONTESTED", trial5)

        # -- round 6: the next decision request reads CONTESTED ---------------------------
        candidate = continue_candidate(chain, artifact_a)
        chain.client.payload = {"kind": "append_paragraph", "text": "第六段：再试一次。",
                                "self_note": "stub: write A v6"}
        out6, view6 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref, "short_rationale": "stub: after counterexample"},
            trigger="evt:v05-continuation:6")
        row6 = offered_row(view6, method_ref)
        check("round 6's decision request reads CONTESTED, count 1 and NOT_HELPED",
              out6.get("outcome") == "ACTED" and bool(row6)
              and row6.get("method_state") == "CONTESTED"
              and int(row6.get("counterexample_count") or 0) == 1
              and (row6.get("evaluation") or {}).get("outcome") == "NOT_HELPED",
              {"state": (row6 or {}).get("method_state"),
               "count": (row6 or {}).get("counterexample_count"),
               "evaluation": (row6 or {}).get("evaluation")})
        check("CONTESTED is not a loss: the handle is still offered",
              method_ref in list(view6.get("method_refs") or [])
              and "withheld_methods" not in view6,
              {"offered": len(view6.get("method_refs") or [])})

        # -- loss: the owner's own retirement (CAS, document kept) ------------------------
        head_before = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        retired = chain.cycle.practice.service.retire_method(
            method_id, expected_head=head_before,
            operation_key="v05-continuation:retire")
        check("the real owner wrote a RETIRED version and kept the document",
              bool(retired.get("ok"))
              and (retired.get("data") or {}).get("method_state") == "RETIRED",
              {"ok": retired.get("ok"), "status": retired.get("status")})

        # -- round 7: still naming the retired handle -> formal refusal ------------------
        refused, view7 = run(chain, decision="NO_ACTION", candidate=None, fields={
            "reason_codes": ["NO_ACTION_CHOSEN"], "method_ref": method_ref,
            "short_rationale": "stub: still naming the retired method"},
            trigger="evt:v05-continuation:7")
        withheld = view7.get("withheld_methods") or {}
        check("the retired handle is refused by AG-1 as INVENTED_METHOD_REF",
              refused.get("outcome") == "DECISION_FAILED"
              and refused.get("detail") == ag1.FAILURE_CLASS_INVENTED_METHOD_REF,
              {"outcome": refused.get("outcome"), "detail": refused.get("detail")})
        check("no Decision was committed for the refused round",
              refused.get("decision") is None and refused.get("decision_route") is None,
              {"decision": refused.get("decision")})
        check("the refused round's model view names the loss instead of hiding it",
              method_ref not in list(view7.get("method_refs") or [])
              and int(withheld.get("count") or 0) >= 1
              and "PRACTICE_METHOD_RETIRED" in (withheld.get("reason_codes") or []),
              {"count": withheld.get("count"), "reasons": withheld.get("reason_codes")})

        # -- round 8: a legal Decision without the old ref still continues A -------------
        candidate = continue_candidate(chain, artifact_a)
        a_before_legal = version(chain.port, artifact_a)
        chain.client.payload = {"kind": "append_paragraph", "text": "第七段：照常写下去。",
                                "self_note": "stub: write A v7"}
        legal, _view8 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "short_rationale": "stub: no method reference"},
            trigger="evt:v05-continuation:8")
        prompt8 = prompt_practice(chain)
        writing_blob = json.dumps(prompt8, ensure_ascii=False)
        check("a legal Decision that does not name the retired handle still commits",
              legal.get("outcome") == "ACTED" and legal.get("decision") == "CONTINUE"
              and legal.get("decision_route") == ag1.ROUTE_COGNITIVE_REQUIRED,
              {key: legal.get(key) for key in ("outcome", "decision", "detail")})
        check("the legal round continues the same work A (B stays put)",
              version(chain.port, artifact_a) == a_before_legal + 1
              and version(chain.port, artifact_b) == b_before,
              {"a": version(chain.port, artifact_a), "b": version(chain.port, artifact_b)})
        check("the writer request carries no retired instruction",
              harness.METHOD_INSTRUCTION not in writing_blob, {"has_instruction": False})
        retired_view = chain.cycle.practice.read_method(method_id) or {}
        check("the retired method's document is still readable (state, not delete)",
              retired_view.get("method_state") == "RETIRED"
              and retired_view.get("instruction") == harness.METHOD_INSTRUCTION
              and bool(str(retired_view.get("excerpt") or "").strip()),
              {"state": retired_view.get("method_state"),
               "has_instruction": retired_view.get("instruction") == harness.METHOD_INSTRUCTION,
               "has_excerpt": bool(str(retired_view.get("excerpt") or "").strip())})

        # -- round 9: a quiet legal round still commits without the old handle ----------
        quiet, _view9 = run(chain, decision="NO_ACTION", candidate=None, fields={
            "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "stub: quiet"},
            trigger="evt:v05-continuation:9")
        check("a quiet legal round still commits",
              quiet.get("outcome") == "NO_ACTION" and quiet.get("decision") == "NO_ACTION",
              {"outcome": quiet.get("outcome"), "decision": quiet.get("decision")})

        print("\nids: A=%s B=%s method=%s retired_head=%s" % (
            bare(artifact_a), bare(artifact_b), bare(method_id), head_before))
        print("versions: A=%d B=%d" % (version(chain.port, artifact_a),
                                       version(chain.port, artifact_b)))
    finally:
        harness.close_chain(chain)

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\nchecks: %d (including %d seeding checks), failed: %d" % (
        len(seed_checks) + len(CHECKS), len(seed_checks), len(failed)))
    if failed:
        for name in failed:
            print("FAILED:", name)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
