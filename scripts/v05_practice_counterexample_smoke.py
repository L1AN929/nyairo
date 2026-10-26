#!/usr/bin/env python3
"""零模型真实 owner 冒烟：两个不同作品上的做法评价、反例、修订与退休。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自己的
   Unix socket、落盘）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、B 的实践工作台、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、
   AR-0 受管作品适配器。Decision 与内容回答是脚本替身，文件里明示，不宣称真实模型自然采用。
2. 第一段情境：项目作品 A 得到真实 owner 版本，Decision 的原始 JSON 带
   ``method_proposal``，由真实 owner 存成 TENTATIVE；同一 Activity 的下一轮命名该方法，
   用 A 自己的结果经历给出 ``HELPED`` → TRIAL。
3. 第二段情境：结束 A 的前台后，用同一轮的 review 选项创建另一件真实作品 B（回顾），
   B 有自己的 owner 版本与 handoff 经历；B 的 Activity 内给出 ``NOT_HELPED``（证据是 B
   自己的结果经历，不是 A 的）→ CONTESTED。两条评价各自引用对应作品的经历与版本。
4. 下一轮正式请求读到 CONTESTED、反例计数 1、有界评价（outcome / 被评价版本 / 证据条数），
   不带 detail；正评价次数没有把反例抹掉，也没有写出任何稳定支持状态。
5. owner ``revise_method``（CAS）显式改指令/范围/未知 → TENTATIVE，旧版与两条评价保留；
   下一轮正式请求与 writer 请求绑定新 head、新指令。
6. 并发旧 head 负控：本轮 Decision 已见过旧 head 后，另一个合法 owner 写者修订；
   本轮 usage 仍绑定旧 head，评价被 owner CAS 拒绝，不静默改绑，反例计数不变。
7. owner ``retire_method`` 后，下一正式回合仍命名旧手柄时 AG-1 拒绝（无 Decision 提交）；
   同一状态下不引用旧手柄的合法 CONTINUE 仍写 B，writer 请求不含退休指令；NO_ACTION 合法。
8. 同一 runtime 内重新装配一个 PracticeSeam，从 owner 重读反例/修订/退休历史；纯读前后
   artifact 版本与 head 不变（0 自动评价、0 写入）。这里不是跨 PID/crash 重启。
9. 所有自有 runtime / 服务在 finally 关闭、lease 释放；夹具根保留便于复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_practice_counterexample_smoke.py
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
NEW_INSTRUCTION = "先写两句结论，再补两段理由"
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


def all_candidates(chain) -> list[dict]:
    import past_candidate_bridge as pcb

    rows: list[dict] = []
    for candidate_ref in chain.runtime.candidate_materializer.build_candidate_set(
            observed_at=TS).candidate_refs:
        row = chain.runtime.candidate_read.get_candidate(str(candidate_ref)) or {}
        rows.append({
            "candidate_ref": str(candidate_ref),
            "candidate_kind": str(row.get("candidate_kind") or ""),
            "source_kind": str(row.get("source_kind") or ""),
            "target": str(pcb.work_ref_from_candidate(
                row, exclusions=chain.cycle._non_work_artifact_ids()) or ""),
        })
    return rows


def continue_candidate(chain, artifact_id: str) -> str:
    """The exact CONTINUE candidate whose owner target is this artifact (whole ref only)."""

    wanted = bare(artifact_id)
    matches = [row["candidate_ref"] for row in all_candidates(chain)
               if row["candidate_kind"] == "CONTINUE_CURRENT"
               and row["source_kind"] == "CURRENT_ACTIVITY"
               and bare(row["target"]) == wanted]
    return matches[0] if len(matches) == 1 else ""


def prompt_practice(chain) -> dict:
    """The practice block of the last real writer request, or ``{}``."""

    if not chain.client.prompts:
        return {}
    try:
        payload = json.loads(chain.client.prompts[-1]["user"])
    except Exception:
        return {}
    value = payload.get("practice_context")
    return dict(value) if isinstance(value, dict) else {}


def install_decision_view_recorder(chain) -> list:
    """Record the practice slice of each real decision request, in order."""

    views: list[dict] = []
    original = chain.cognition.invoke

    def invoke(request, *, schema_retry_index: int = 0):
        situation = getattr(request, "situation_context", {}) or {}
        views.append(dict((situation.get("practice") or {})))
        return original(request, schema_retry_index=schema_retry_index)

    chain.cognition.invoke = invoke
    return views


def run(chain, *, decision: str, candidate, fields: dict, trigger: str):
    """Drive one real ``evaluate_once``; return (round report, this round's practice view)."""

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


def experience_for(chain, artifact_id: str) -> dict:
    """The readable experience naming this artifact as its own owner result, or ``{}``."""

    wanted = bare(artifact_id)
    matches = [row for row in chain.cycle.practice.experiences()
               if bare(row.get("artifact_ref")) == wanted]
    return matches[0] if len(matches) == 1 else {}


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import agency_decision_ag1 as ag1
    import life_practice_context as lpc
    import result_method_behavior_smoke as harness

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("v05-counterexample")
    chain.decision_views = install_decision_view_recorder(chain)
    try:
        # -- situation 1: work A really written; the Decision's own proposal saved -----
        seed = harness.seed_method_after_first_write(chain)
        seed_checks = list(harness._CHECKS)
        method_id = str(seed.method_id)
        method_ref = str(seed.method_ref)
        artifact_a = str((seed.round_out.get("step_receipt") or {}).get("artifact_id") or "")
        experience_a = str(seed.experience_id)
        a_after_first = version(chain.port, artifact_a)
        check("situation 1 wrote a real work A through the owner",
              bool(artifact_a) and a_after_first >= 2,
              {"artifact": artifact_a, "version": a_after_first})
        check("situation 1 produced its own readable experience",
              bool(experience_for(chain, artifact_a)), {"experience": experience_a})

        # -- round 2: continue A; HELPED verdict backed by A's own result ---------------
        candidate = continue_candidate(chain, artifact_a)
        check("round 2 is offered the exact CONTINUE candidate for A", bool(candidate),
              {"artifact": artifact_a, "candidates": len(all_candidates(chain))})
        chain.client.payload = {"kind": "append_paragraph", "text": "第二段：先写结论再补理由。",
                                "self_note": "stub: write A v2"}
        out2, _view2 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "A 上先写结论确实顺一些",
                             "experience_ids": [experience_a]},
            "short_rationale": "stub: verdict on A"}, trigger="evt:v05-ce:2")
        usage2 = out2.get("method_usage") or {}
        trial2 = out2.get("method_trial") or {}
        stored2 = chain.cycle.practice.read_method(method_id)
        check("round 2 acts through the formal chain and writes A",
              out2.get("outcome") == "ACTED" and version(chain.port, artifact_a) == a_after_first + 1,
              {key: out2.get(key) for key in ("outcome", "detail", "practice_error")})
        check("round 2's HELPED verdict reaches TRIAL with readable evidence",
              trial2.get("outcome") == "HELPED" and trial2.get("method_state") == "TRIAL"
              and trial2.get("evidence_usable") is True, trial2)
        check("the HELPED verdict names the round's offered version",
              len(stored2.get("trials") or []) == 1
              and stored2["trials"][0].get("version_id") == usage2.get("version_ref"),
              {"trial": (stored2.get("trials") or [{}])[0].get("version_id"),
               "offered": usage2.get("version_ref")})
        check("the HELPED verdict cites A's own result experience",
              (stored2.get("trials") or [{}])[0].get("experience_ids") == [experience_a],
              stored2.get("trials"))
        check("round 2's consequence refs are A's real result, not the method",
              work_ref(artifact_a) in (usage2.get("consequence_refs") or [])
              and bare(method_id) not in (usage2.get("consequence_refs") or []), usage2)

        # -- end A's foreground; the review option is a real, other choice this round --
        ended = harness.end_foreground(chain, tag="a-done")
        options = chain.cycle.project_options()
        review_options = [row for row in options if row.get("kind") == "REVIEW_FROM_EXPERIENCE"]
        check("ending A leaves other real options on the table",
              ended.get("outcome") == "ABANDONED" and bool(review_options),
              {"ended": ended.get("outcome"),
               "kinds": [row.get("kind") for row in options]})

        # -- situation 2: a different real work B (a review) with its own owner result --
        harness.select_option(chain, review=True)
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾：那次卡住的原因。",
                                "self_note": "stub: write B"}
        out_b1 = chain.cycle.evaluate_once(trigger_ref="evt:v05-ce:b1", act=True)
        artifact_b = str((out_b1.get("step_receipt") or {}).get("artifact_id") or "")
        handoff_b1 = out_b1.get("experience_handoff") or {}
        experience_b = str((experience_for(chain, artifact_b) or {}).get("experience_id") or "")
        b_after_first = version(chain.port, artifact_b)
        check("situation 2 created a different real work B through the owner",
              out_b1.get("outcome") == "ACTED" and bool(artifact_b)
              and bare(artifact_b) != bare(artifact_a) and b_after_first >= 2,
              {"artifact": artifact_b, "version": b_after_first})
        check("B's result has its own accepted handoff and readable experience",
              str(handoff_b1.get("disposition") or "") == "ACCEPT" and bool(experience_b)
              and experience_b != experience_a,
              {"handoff": handoff_b1.get("handoff_id"), "experience": experience_b})
        check("A and B are different owner results, not one result renamed",
              bare(experience_for(chain, artifact_a).get("version_ref") or "")
              != bare(experience_for(chain, artifact_b).get("version_ref") or "")
              and work_ref(artifact_a) != work_ref(artifact_b),
              {"a": experience_for(chain, artifact_a),
               "b": experience_for(chain, artifact_b)})

        # -- round 4: continue B; NOT_HELPED backed by B's own result ------------------
        candidate = continue_candidate(chain, artifact_b)
        check("round 4 is offered the exact CONTINUE candidate for B", bool(candidate),
              {"artifact": artifact_b})
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾第二段：换个写法试试。",
                                "self_note": "stub: write B v2"}
        out4, _view4 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "NOT_HELPED",
                             "detail": "回顾时先写结论反而卡住",
                             "experience_ids": [experience_b]},
            "short_rationale": "stub: counterexample on B"}, trigger="evt:v05-ce:4")
        usage4 = out4.get("method_usage") or {}
        trial4 = out4.get("method_trial") or {}
        stored4 = chain.cycle.practice.read_method(method_id)
        check("round 4's counterexample moves the method to CONTESTED",
              trial4.get("outcome") == "NOT_HELPED"
              and trial4.get("method_state") == "CONTESTED"
              and trial4.get("evidence_usable") is True, trial4)
        check("...and writes B, leaving A's version alone",
              out4.get("outcome") == "ACTED"
              and version(chain.port, artifact_b) == b_after_first + 1
              and version(chain.port, artifact_a) == a_after_first + 1,
              {"a": version(chain.port, artifact_a), "b": version(chain.port, artifact_b)})
        trials4 = list(stored4.get("trials") or [])
        check("the two verdicts cite different works' own experiences",
              len(trials4) == 2
              and trials4[0].get("experience_ids") == [experience_a]
              and trials4[1].get("experience_ids") == [experience_b],
              [row.get("experience_ids") for row in trials4])
        check("the counterexample names B's offered version",
              len(trials4) == 2 and trials4[1].get("version_id") == usage4.get("version_ref"),
              {"trial": (trials4[-1] if trials4 else {}).get("version_id"),
               "offered": usage4.get("version_ref")})
        check("the counterexample count is one and the HELPED verdict is not erased",
              int(stored4.get("counterexamples") or 0) == 1,
              stored4.get("counterexamples"))

        # -- round 5: the next decision really reads CONTESTED + bounded evaluation ----
        candidate = continue_candidate(chain, artifact_b)
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾第三段：再试一次。",
                                "self_note": "stub: write B v3"}
        out5, view5 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref, "short_rationale": "stub: after counterexample"},
            trigger="evt:v05-ce:5")
        row5 = offered_row(view5, method_ref)
        evaluation5 = (row5 or {}).get("evaluation") or {}
        check("round 5 reads CONTESTED, count 1 and the explicit NOT_HELPED verdict",
              out5.get("outcome") == "ACTED" and bool(row5)
              and row5.get("method_state") == "CONTESTED"
              and int(row5.get("counterexample_count") or 0) == 1
              and evaluation5.get("outcome") == "NOT_HELPED",
              {"state": (row5 or {}).get("method_state"),
               "count": (row5 or {}).get("counterexample_count"),
               "evaluation": evaluation5})
        check("the bounded evaluation points at the version the verdict was about",
              evaluation5.get("version_id") == usage4.get("version_ref")
              and int(evaluation5.get("evidence_count") or 0) == 1, evaluation5)
        leaked = json.dumps(row5 or {}, ensure_ascii=False)
        check("the round view leaks no trial detail prose",
              "回顾时先写结论反而卡住" not in leaked and "A 上先写结论确实顺一些" not in leaked
              and "detail" not in evaluation5, {"has_detail_key": "detail" in evaluation5})
        check("a counterexample is a state, not a loss: the handle is still offered",
              method_ref in list(view5.get("method_refs") or [])
              and "withheld_methods" not in view5, {"offered": len(view5.get("method_refs") or [])})

        # -- explicit owner revision: CAS, TENTATIVE, history kept ---------------------
        head_before_revise = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        revised = chain.cycle.practice.service.revise_method(
            method_id, instruction=NEW_INSTRUCTION, scope="长文与回顾",
            uncertainty="不知道是否适用短文本", expected_head=head_before_revise,
            operation_key="v05-ce:revise")
        revised_data = revised.get("data") or {}
        new_head = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        check("the owner revision moves the head and returns TENTATIVE",
              bool(revised.get("ok")) and revised_data.get("method_state") == "TENTATIVE"
              and new_head != head_before_revise,
              {"ok": revised.get("ok"), "state": revised_data.get("method_state")})
        check("the revision keeps both verdicts and the counterexample",
              int(revised_data.get("trials_preserved") or 0) == 2
              and revised_data.get("previous", {}).get("instruction") == harness.METHOD_INSTRUCTION,
              revised_data)
        stored_revised = chain.cycle.practice.read_method(method_id)
        check("the old verdicts still name their original versions",
              len(stored_revised.get("trials") or []) == 2
              and stored_revised["trials"][0].get("version_id") == usage2.get("version_ref")
              and stored_revised["trials"][1].get("version_id") == usage4.get("version_ref"),
              [row.get("version_id") for row in stored_revised.get("trials") or []])

        # -- round 6: next formal request + writer bind the NEW head -------------------
        candidate = continue_candidate(chain, artifact_b)
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾第四段：按修订做法写。",
                                "self_note": "stub: write B v4"}
        out6, view6 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref, "short_rationale": "stub: after revision"},
            trigger="evt:v05-ce:6")
        row6 = offered_row(view6, method_ref)
        writer6 = prompt_practice(chain)
        methods6 = writer6.get("methods") or []
        check("round 6's decision request binds the revised head and instruction",
              out6.get("outcome") == "ACTED" and bool(row6)
              and row6.get("method_state") == "TENTATIVE"
              and row6.get("head_version_id") == new_head
              and row6.get("instruction") == NEW_INSTRUCTION
              and int(row6.get("counterexample_count") or 0) == 1,
              {"row": {key: (row6 or {}).get(key) for key in
                       ("method_state", "head_version_id", "instruction", "counterexample_count")}})
        check("round 6's formal writer request carries the revised instruction",
              len(methods6) == 1 and methods6[0].get("instruction") == NEW_INSTRUCTION
              and methods6[0].get("method_ref") == method_ref,
              {"methods": len(methods6),
               "instruction_matches": bool(methods6)
               and methods6[0].get("instruction") == NEW_INSTRUCTION})
        check("the bounded evaluation still reports the verdict's own version",
              ((row6 or {}).get("evaluation") or {}).get("version_id") == usage4.get("version_ref"),
              (row6 or {}).get("evaluation"))

        # -- stale-head negative control: another owner writer revises mid-round --------
        offered_head6 = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        real_writing = chain.cycle.practice_writing_context
        state = {"revised": False}

        def writing_with_concurrent_revision(artifact_id=""):
            if not state["revised"]:
                answer = chain.cycle.practice.service.revise_method(
                    method_id, instruction="另一个写者改过的做法",
                    expected_head=offered_head6,
                    operation_key="v05-ce:revise:concurrent")
                state["revised"] = bool(answer.get("ok"))
            return real_writing(artifact_id)

        chain.cycle.practice_writing_context = writing_with_concurrent_revision
        candidate = continue_candidate(chain, artifact_b)
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾第五段：并发修订之后。",
                                "self_note": "stub: write B v5"}
        try:
            out7, _view7 = run(chain, decision="CONTINUE", candidate=candidate, fields={
                "method_ref": method_ref,
                "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                                 "detail": "并发轮还想评价旧版本",
                                 "experience_ids": [experience_b]},
                "short_rationale": "stub: stale head"}, trigger="evt:v05-ce:7")
        finally:
            chain.cycle.practice_writing_context = real_writing
        usage7 = out7.get("method_usage") or {}
        trial7 = out7.get("method_trial") or {}
        stored7 = chain.cycle.practice.read_method(method_id)
        check("the concurrent owner writer really moved the head",
              state["revised"] and str(stored7.get("head_version_id")) != offered_head6,
              {"revised": state["revised"], "head": stored7.get("head_version_id")})
        check("this round's usage stays bound to the head it actually saw",
              usage7.get("version_ref") == offered_head6, usage7)
        check("a verdict about the unseen head is refused by the owner",
              trial7.get("status") == "REFUSED", trial7)
        check("the refused verdict writes nothing and keeps the counterexample",
              len(stored7.get("trials") or []) == 2
              and int(stored7.get("counterexamples") or 0) == 1,
              {"trials": len(stored7.get("trials") or []),
               "counterexamples": stored7.get("counterexamples")})
        check("the refused round still wrote B",
              out7.get("outcome") == "ACTED", out7.get("detail"))

        # -- retirement: the handle leaves the offer, the document stays ----------------
        head_before_retire = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        retired = chain.cycle.practice.service.retire_method(
            method_id, expected_head=head_before_retire,
            operation_key="v05-ce:retire")
        check("the real owner wrote a RETIRED version and kept the document",
              bool(retired.get("ok"))
              and (retired.get("data") or {}).get("method_state") == "RETIRED"
              and (retired.get("data") or {}).get("trials_preserved") == 2,
              {"ok": retired.get("ok"), "data": retired.get("data")})

        # round 8: still naming the retired handle -> formal refusal, no Decision
        refused, view8 = run(chain, decision="NO_ACTION", candidate=None, fields={
            "reason_codes": ["NO_ACTION_CHOSEN"], "method_ref": method_ref,
            "short_rationale": "stub: still naming the retired method"},
            trigger="evt:v05-ce:8")
        withheld8 = view8.get("withheld_methods") or {}
        check("a retired handle is refused by AG-1 as INVENTED_METHOD_REF",
              refused.get("outcome") == "DECISION_FAILED"
              and refused.get("detail") == ag1.FAILURE_CLASS_INVENTED_METHOD_REF,
              {"outcome": refused.get("outcome"), "detail": refused.get("detail")})
        check("no Decision was committed for the refused round",
              refused.get("decision") is None and refused.get("decision_route") is None,
              {"decision": refused.get("decision")})
        check("the refused round's view names the loss instead of hiding it",
              method_ref not in list(view8.get("method_refs") or [])
              and int(withheld8.get("count") or 0) >= 1
              and "PRACTICE_METHOD_RETIRED" in (withheld8.get("reason_codes") or []),
              {"count": withheld8.get("count"), "reasons": withheld8.get("reason_codes")})

        # round 9: a legal Decision without the retired handle still writes B
        candidate = continue_candidate(chain, artifact_b)
        b_before_legal = version(chain.port, artifact_b)
        chain.client.payload = {"kind": "append_paragraph", "text": "回顾第六段：照常写。",
                                "self_note": "stub: write B v6"}
        legal, _view9 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "short_rationale": "stub: no method reference"},
            trigger="evt:v05-ce:9")
        writer9 = json.dumps(prompt_practice(chain), ensure_ascii=False)
        check("a legal Decision without the retired handle still commits",
              legal.get("outcome") == "ACTED" and legal.get("decision") == "CONTINUE"
              and legal.get("decision_route") == ag1.ROUTE_COGNITIVE_REQUIRED,
              {key: legal.get(key) for key in ("outcome", "decision", "detail")})
        check("the legal round writes B and the writer carries no retired instruction",
              version(chain.port, artifact_b) == b_before_legal + 1
              and harness.METHOD_INSTRUCTION not in writer9
              and NEW_INSTRUCTION not in writer9,
              {"writes": version(chain.port, artifact_b) - b_before_legal})

        # round 10: a quiet legal round stays legal
        quiet, _view10 = run(chain, decision="NO_ACTION", candidate=None, fields={
            "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "stub: quiet"},
            trigger="evt:v05-ce:10")
        check("NO_ACTION remains a legal outcome after retirement",
              quiet.get("outcome") == "NO_ACTION" and quiet.get("decision") == "NO_ACTION",
              {"outcome": quiet.get("outcome"), "decision": quiet.get("decision")})

        # -- re-assembly inside the same runtime: read history from the owner, 0 writes --
        before_head = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        before_a = version(chain.port, artifact_a)
        before_b = version(chain.port, artifact_b)
        fresh_seam = lpc.PracticeSeam(chain.cycle.practice.service,
                                      memory=chain.cycle.practice.memory)
        history = fresh_seam.read_method(method_id) or {}
        also = fresh_seam.read_method(method_id) or {}
        after_head = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        check("a re-assembled seam re-reads the owner's own history",
              history.get("method_state") == "RETIRED"
              and len(history.get("trials") or []) == 2
              and int(history.get("counterexamples") or 0) == 1
              and history.get("instruction") == "另一个写者改过的做法",
              {"state": history.get("method_state"),
               "trials": len(history.get("trials") or []),
               "counterexamples": history.get("counterexamples"),
               "instruction": history.get("instruction")})
        check("reading the history is pure: no version, head or trial moved",
              before_head == after_head
              and version(chain.port, artifact_a) == before_a
              and version(chain.port, artifact_b) == before_b
              and also.get("head_version_id") == history.get("head_version_id"),
              {"head": after_head, "a": version(chain.port, artifact_a),
               "b": version(chain.port, artifact_b)})
        check("the retired document is still readable (state, not delete)",
              bool(str(history.get("excerpt") or "").strip())
              and history.get("instruction") == "另一个写者改过的做法",
              {"excerpt": bool(str(history.get("excerpt") or "").strip())})

        print("\nids: A=%s B=%s method=%s" % (
            bare(artifact_a), bare(artifact_b), bare(method_id)))
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
