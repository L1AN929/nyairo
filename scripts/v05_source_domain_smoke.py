#!/usr/bin/env python3
"""v0.5 零模型真 owner 冒烟：来源拒读不跨域伪造方法退休（单整链）。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发、0 Memory 治理改动：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自有
   AF_UNIX + 落盘 canonical）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、真实 Practice / Sharing owner、AG-0、AG-1 原始 JSON 校验、
   AG-2 adoption、AR-0 受管作品适配器。Decision 与内容回答是脚本替身（明示）。
2. 第一轮由夹具自己的正式 Decision 写出作品 A 的真实版本、得到 owner 结果身份与可回忆
   经历，并由 Decision 的原始 ``method_proposal`` 经 AG-1/AG-2 让真实 Practice owner
   存成 TENTATIVE（不是手造方法状态）。
3. 下一正式 Decision ``CONTINUE`` 同一作品 A 并带 ``HELPED`` + 真经历 id：真实 owner
   把做法推进 TRIAL，判据来自 M0 可读证据（与作品来源读权限无关）。
4. 正式 Decision 从 A 的当前 head 显式 prepare 草稿 D（D 绑定 A，A 不变）。
5. 正常正式请求：同一请求读到 A 来源与方法 TRIAL（有界评价）。
6. 用前块已存在的 ``SourceRefusingPort`` **只**拒 artifact A 的来源读（明确是故障注入，
   不是实际撤权，也不做 Memory controls/delete）：下一完整正式请求里 D 的正文被扣留
   （``text_withheld`` / ``text_available`` / ``SOURCE_GOVERNED``），sentinel 不在请求、
   读回或拒绝答案里；显式 send_local 由 owner 拒且零写。
7. 同一正式请求核对方法域：M0 经历仍可读、真实 Practice owner 的 method_state 仍是 TRIAL，
   不因作品来源拒读被自动退休；若本轮 offered 视图本身扣留做法，只报告实情，不修改。
8. 关闭拒读后，下一合法无 sharing 的 NO_ACTION 从真 owner 重新读到 A/D 正文，D 的
   state/head/receipt 不变，零自动 send/drop/rebind；只是暂时故障恢复，不称撤权被逆转。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_source_domain_smoke.py
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

#: 只进入"来源不可读"草稿的句子片段；只用于断言扣留，绝不打印。
SENTINEL = "SENTINEL_V05_SOURCE_DOMAIN_DO_NOT_RETURN"
D_TEXT = "这一句从可读作品里留下，用来观察来源被拒读时正文是否被扣住。" + SENTINEL

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-70s %s" % (name, "PASS" if condition else "FAIL"))
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


def head_version(chain, artifact_id) -> str:
    point = chain.port.resume_point(artifact_id)
    return str(getattr(point, "head_version_id", "") or "")


def continue_candidate(chain, artifact_id, observed_at: str) -> str | None:
    import past_candidate_bridge as pcb

    wanted = bare(artifact_id)
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at).candidate_refs
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


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import life_sharing_context as lsc
    import life_sharing_service as lss
    import result_method_behavior_smoke as harness
    import v05_result_sharing_continuation_smoke as sharing_ref

    class DomainCognition(sharing_ref.PlanCognition):
        """分享计划替身 + 每轮真实请求里的 practice 切片（模型当时看到的那份）。"""

        def invoke(self, request, *, schema_retry_index: int = 0):
            result = super().invoke(request, schema_retry_index=schema_retry_index)
            situation = getattr(request, "situation_context", {}) or {}
            practice = situation.get("practice") if isinstance(situation, dict) else {}
            self.captured[-1]["practice"] = json.loads(
                json.dumps(practice if isinstance(practice, dict) else {},
                           ensure_ascii=False, default=str))
            return result

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("v05-source-domain")
    completed = False
    planner = DomainCognition()
    try:
        # -- 0) 真实作品 A + Decision 自己的方法提案（TENTATIVE） ----------------------
        seed = harness.seed_method_after_first_write(chain)
        method_id = str(seed.method_id)
        method_ref = str(seed.method_ref)
        artifact_a = str((seed.round_out.get("step_receipt") or {}).get("artifact_id") or "")
        check("the fixture's own formal round really wrote work A and proposed the method",
              bool(artifact_a) and bool(method_id)
              and all(ok for _n, ok, _d in harness._CHECKS),
              {"artifact": artifact_a, "method": method_id})

        # -- 1) 正式 CONTINUE + 有证 HELPED → 真实 owner 推进 TRIAL --------------------
        stamp = sharing_ref.next_instant()
        candidate = continue_candidate(chain, artifact_a, stamp)
        check("a formal CONTINUE candidate for A exists", bool(candidate), candidate)
        chain.client.payload = {"kind": "append_paragraph", "text": "第二段：顺着刚才那句写。",
                                "self_note": "stub: write A next"}
        chain.cognition.script = {
            "decision": "CONTINUE", "selected_candidate_id": candidate,
            "deferred_candidate_ids": [], "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "这一次顺着写确实更顺",
                             "experience_ids": [str(seed.experience_id)]},
            "short_rationale": "stub: evidenced trial"}
        out_trial = chain.cycle.evaluate_once(trigger_ref="evt:v05-source-domain:trial",
                                             act=True, observed_at=stamp)
        stored = chain.cycle.practice.read_method(method_id) or {}
        check("the real owner moved the method to TRIAL on the readable evidence",
              (out_trial.get("method_trial") or {}).get("outcome") == "HELPED"
              and stored.get("method_state") == "TRIAL"
              and len(stored.get("trials") or []) == 1,
              {"trial": out_trial.get("method_trial"), "state": stored.get("method_state")})
        a_after_trial = head_version(chain, artifact_a)

        # -- 2) 正式 Decision 显式 prepare D（绑定 A 的当前 head） ---------------------
        chain.cognition.invoke = planner.invoke
        service = chain.cycle.sharing_service
        check("the cycle is wired to the real sharing owner",
              isinstance(service, lss.LifeSharingService), type(service).__name__)
        out_prepare = sharing_ref.run_round(
            chain, planner, {"op": "prepare", "text": D_TEXT, "title": "来源域草稿"},
            trigger="evt:v05-source-domain:prepare")
        prepared = out_prepare.get("sharing") or {}
        draft_d = str(prepared.get("share_id") or "")
        draft_head = str(prepared.get("head_version_id") or "")
        owner_d = service.read_share(draft_d, include_text=False) if draft_d else {}
        d_source = ((owner_d.get("data") or {}).get("share") or {}).get("source") or {}
        d_bound = str(d_source.get("version_id") or "")
        # The cycle's own work target is the source it actually offered (the receipt artifact
        # and the current work can differ in this fixture); the refusal must name *that* one.
        work_artifact = str(d_source.get("artifact_id") or "")
        offered_source = next((row for row in
                               (sharing_ref.latest_sharing_block(planner).get("sources") or [])
                               if isinstance(row, dict)), {})
        check("prepare creates a real DRAFT bound to the work the request offered",
              prepared.get("ok") is True and prepared.get("state") == lss.STATE_DRAFT
              and bool(draft_d) and bool(d_bound) and bool(work_artifact)
              and bare(offered_source.get("artifact_id")) == bare(work_artifact),
              {"draft": draft_d, "source": d_source, "offered": offered_source})
        draft_state = lsc.read_draft(service, draft_d)
        d_receipt = draft_state.get("receipt")
        d_work_version = str(d_bound or "")

        # -- 3) prepare 这一轮的正式请求已经读到 A 来源与 TRIAL 方法 -------------------
        normal_block = sharing_ref.latest_sharing_block(planner)
        normal_practice = json.loads(json.dumps(planner.captured[-1].get("practice") or {},
                                                ensure_ascii=False, default=str))
        normal_methods = [row for row in (normal_practice.get("methods") or [])
                          if isinstance(row, dict) and row.get("method_ref") == method_ref]
        check("the formal request really read A as a source",
              any(str(row.get("artifact_id") or "") == work_ref(work_artifact)
                  for row in (normal_block.get("sources") or []) if isinstance(row, dict)),
              {"sources": normal_block.get("sources")})
        check("...and the same request offers the method at TRIAL",
              bool(normal_methods) and normal_methods[0].get("method_state") == "TRIAL",
              normal_methods)

        # -- 4) 只拒 A 来源读（故障注入，不是撤权）：正文扣留、方法域不被牵连 ----------
        # 端口注入放在 cycle 实际持有的同一个 sharing owner 上：动作路径与提示读路径
        # 都经由 ``service.projects``，换一个 service 包装层会漏掉动作侧的来源复查。
        original_projects = service.projects
        refusing = sharing_ref.SourceRefusingPort(original_projects, source_id=work_artifact)
        service.projects = refusing
        governed = service
        check("the M0 experience is still readable while A's source read is refused",
              bool(chain.cycle.practice.read_experience(seed.experience_id)),
              {"experience": seed.experience_id})
        check("the practice owner still reports TRIAL before the governed round",
              (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL")

        sharing_ref.run_round(chain, planner, {"op": "none"},
                              trigger="evt:v05-source-domain:governed-read")
        governed_block = sharing_ref.latest_sharing_block(planner)
        governed_practice = json.loads(json.dumps(planner.captured[-1].get("practice") or {},
                                                 ensure_ascii=False, default=str))
        governed_row = (sharing_ref.draft_rows(planner, draft_d) or [{}])[0]
        block_text = json.dumps(governed_block, ensure_ascii=False, default=str)
        bounded_text = json.dumps(planner.captured[-1]["bounded"], ensure_ascii=False,
                                  default=str)
        practice_text = json.dumps(governed_practice, ensure_ascii=False, default=str)
        check("the formal request names the withholding with the owner's own reason",
              governed_row.get("text_withheld") is True
              and governed_row.get("text_available") is False
              and governed_row.get("withheld_reason_code") == "SOURCE_GOVERNED"
              and not governed_row.get("text_excerpt"), governed_row)
        check("the withheld sentinel is unrecoverable from request / bounded / practice",
              SENTINEL not in block_text and SENTINEL not in bounded_text
              and SENTINEL not in practice_text)
        governed_owner_read = governed.read_share(draft_d, include_text=True)
        check("the owner's own read withholds too, and never says 'not found'",
              governed_owner_read.get("ok") is True
              and (governed_owner_read.get("data") or {}).get("text_withheld") is True
              and SENTINEL not in json.dumps(governed_owner_read, ensure_ascii=False,
                                             default=str)
              and str(governed_owner_read.get("reason_code") or "") != "SOURCE_NOT_FOUND",
              {"state": governed_owner_read.get("state"),
               "reason_code": governed_owner_read.get("reason_code")})

        # -- 5) 显式 send_local：owner 拒、零写、sentinel 不泄漏 ----------------------
        out_refused = sharing_ref.run_round(
            chain, planner, {"op": "send_local", "target": draft_d},
            trigger="evt:v05-source-domain:governed-send")
        refused = out_refused.get("sharing") or {}
        after_refused = governed.read_share(draft_d, include_text=False)
        check("the owner refuses the send with a named refusal and zero writes",
              refused.get("ok") is False and refused.get("delivered") is False
              and str(refused.get("status") or "") == lss.STATUS_STALE_SOURCE
              and str(refused.get("reason_code") or "") not in ("", "SOURCE_NOT_FOUND")
              and SENTINEL not in json.dumps(refused, ensure_ascii=False, default=str)
              and after_refused["data"]["share"].get("head_version_id") == draft_head
              and after_refused["data"]["share"].get("state") == lss.STATE_DRAFT
              and after_refused["data"].get("receipt") is None, refused)

        # -- 6) 方法域独立：A 拒读不自动退休，M0 证据仍可读 --------------------------
        governed_method = chain.cycle.practice.read_method(method_id) or {}
        governed_rows = [row for row in (governed_practice.get("methods") or [])
                         if isinstance(row, dict) and row.get("method_ref") == method_ref]
        withheld_methods = governed_practice.get("withheld_methods") or {}
        check("a refused work source never auto-retires the method in the practice domain",
              governed_method.get("method_state") == "TRIAL"
              and "PRACTICE_METHOD_RETIRED"
              not in list(withheld_methods.get("reason_codes") or [])
              and bool(chain.cycle.practice.read_experience(seed.experience_id)),
              {"state": governed_method.get("method_state"),
               "withheld_reasons": withheld_methods.get("reason_codes")})
        check("if the governed view offers the method, it is still TRIAL (no faked verdict)",
              not governed_rows or governed_rows[0].get("method_state") == "TRIAL",
              governed_rows)

        # -- 7) 故障解除：下一合法 NO_ACTION 从 owner 重读到正文，零自动动作 ----------
        service.projects = original_projects
        chain.cycle.sharing_service = service
        head_before_recover = lsc.read_draft(service, draft_d).get("head_version_id")
        out_recover = sharing_ref.run_round(chain, planner, {"op": "none"},
                                            trigger="evt:v05-source-domain:recovered")
        recovered_ctx = chain.cycle.sharing_decision_context()
        recovered_row = next((row for row in (recovered_ctx.get("shares") or [])
                              if str(row.get("share_id") or "") == draft_d), {})
        recovered_state = lsc.read_draft(service, draft_d)
        check("after the injection ends the owner's readable text returns",
              bool(recovered_ctx) and recovered_row.get("text_available") is True
              and bool(recovered_row.get("text_excerpt"))
              and bool(head_version(chain, work_artifact)),
              {"text_available": recovered_row.get("text_available"),
               "text_chars": recovered_row.get("text_chars")})
        check("nothing was auto-sent, dropped or rebound by the recovery round",
              (out_recover.get("sharing") in (None, {}))
              and recovered_state.get("head_version_id") == head_before_recover
              and recovered_state.get("state") == lss.STATE_DRAFT
              and recovered_state.get("receipt") is None
              and d_receipt is None,
              {"state": recovered_state.get("state"),
               "head": recovered_state.get("head_version_id")})
        check("the method domain is unchanged by the temporary fault",
              (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL"
              and bool(chain.cycle.practice.read_experience(seed.experience_id)))
        check("only a temporary fault was exercised: the owner still reads A directly",
              bool(head_version(chain, work_artifact))
              and refusing.refused_reads >= 1,
              {"refused_reads": refusing.refused_reads})

        completed = True
        return 0 if all(ok for _name, ok, _detail in CHECKS) else 1
    finally:
        try:
            harness.close_chain(chain)
        except Exception as exc:  # noqa: BLE001 - printed, not hidden
            print("  chain close error:", type(exc).__name__, str(exc)[:200])
        failed = [name for name, ok, _detail in CHECKS if not ok]
        print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
        for name in failed:
            print("FAILED:", name)
        print("RESULT: %s" % ("PASS" if completed and not failed else
                              ("FAIL" if completed else "ERROR")))


if __name__ == "__main__":
    sys.exit(main())
