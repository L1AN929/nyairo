#!/usr/bin/env python3
"""v0.5 零模型真 owner 冒烟：同一结果作品——方法证据——草稿的来源损失（单整链）。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发、0 Memory 治理改动：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自有
   AF_UNIX + 落盘 canonical）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、真实 Practice / Sharing owner、AG-0、AG-1 原始 JSON 校验、
   AG-2 adoption、AR-0 受管作品适配器。Decision 与内容回答是脚本替身（明示）。
2. 第一轮由夹具自己的正式 Decision 真正写出作品 A 的一个版本，得到 owner 结果身份
   ``R = artifact:<A>@artifactversion:<head>``、step receipt 与可回忆经历；同一轮的
   ``method_proposal`` 经 AG-1/AG-2 由真实 Practice owner 存成 TENTATIVE。
3. 正式 NO_ACTION + ``method_ref`` + ``method_trial``：只用**来源对应 R** 的真实可读
   经历记一次 HELPED，真实 owner 把方法推进 TRIAL。核 Memory 安全 metadata 的
   ``artifact_ref``/``version_ref``/``owner_result`` 与 receipt 的 A/R 完全一致；
   trial 的 ``experience_ids`` 就是该经历 id，``version_id`` 是受评**方法**版本，
   与作品版本 R 分开，不混用。
4. 正式 prepare 草稿 D：显式选择同一 A/R（本轮实际请求里的 current_work 来源），
   owner 读回 ``source.artifact_id == A``、``source.version_id == R``；D 是另一件
   artifact，A 的 head 不变。健康基线的同一轮真实请求同时带到 D、方法行与显式评价。
5. 只对 **A 的来源读** 注入 ``SourceRefusingPort``（既有故障形状，明确不是实际撤权，
   也不动 Memory controls/删除），注入在 cycle 实际持有的同一个 Sharing owner 上：
   下一正式请求里 D 正文被扣留并给出 owner 自己的 ``SOURCE_GOVERNED``，sentinel 不在
   请求 / bounded / practice / owner 读回里；显式 send_local 由 owner 拒且零写。
6. 同一受限回合核对方法域（非空观测）：M0 该经历仍可读、真实 Practice owner 仍是
   TRIAL、最终方法行确实存在且 head/状态与 owner 一致，评价详情按既有治理如实
   （可用则有界评价事实，不可用则具名 withheld 理由），不把作品来源拒读当成
   Memory 证据失效，也不自动退休方法。
7. 故障解除后下一合法无 sharing 回合从真 owner 重读：A/R/D 绑定不变、正文恢复、
   零自动 send/drop/rebind。只是暂时故障恢复，不称撤权逆转。
8. 同一真实 owner 合法 ``archive_artifact`` A（只本单创建对象，非删 root）：版本/
   head/正文保留；正式当前目标 A 不可续，不 fallback 到另建的可续 B（若 cycle 在
   AG-1 前就拒，则如实分层，不冒已提交 CONTINUE）；D 来源 ARCHIVED 仍可读但 owner
   发送门拒 ``SOURCE_ARCHIVED`` 零写；方法证据域仍 TRIAL、不自动 RETIRED。
9. 最后合法无 sharing 的 NO_ACTION：零写、不强制切事/退休/复活 A。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_same_result_loss_smoke.py
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
SENTINEL = "SENTINEL_V05_SAME_RESULT_LOSS_DO_NOT_RETURN"
D_TEXT = "这一句来自刚刚写完的那一版，用来观察同一作品的草稿在来源被拒读时如何停顿。" + SENTINEL
B_TEXT = "另一件本来可以继续的作品。"
ARCHIVE_KEY = "op:v05-same-result-loss:archive-a"
CREATE_B_KEY = "op:v05-same-result-loss:create-b"

CHECKS: list[tuple[str, bool, object]] = []
ROUNDS: list[dict] = []
TABLE: list[dict] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-74s %s" % (name, "PASS" if condition else "FAIL"))
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


def meta(out: dict, extra: dict | None = None) -> dict:
    """Safe structural row: no private text, no prompt body."""

    sharing = out.get("sharing") if isinstance(out.get("sharing"), dict) else {}
    row = {
        "decision": str(out.get("decision"))[:48],
        "outcome": str(out.get("outcome") or "")[:48],
        "sharing_ok": sharing.get("ok"),
        "sharing_status": str(sharing.get("status") or "")[:32],
        "sharing_reason": str(sharing.get("reason_code") or "")[:48],
        "method_trial": str((out.get("method_trial") or {}).get("status")
                            or (out.get("method_trial") or {}).get("method_state") or "")[:32],
        "detail": str(out.get("detail") or out.get("practice_error") or "")[:160],
    }
    row.update(extra or {})
    return row


class SameResultCognition:
    """分享计划替身 + 每轮真实请求里的 practice 切片（模型当时看到的那份）。

    Delegates the plan/step handling to the existing ``PlanCognition`` by subclassing it in
    :func:`main` (the module is imported lazily there); this class only adds the practice
    capture on top of the same request.
    """

    def invoke(self, request, *, schema_retry_index: int = 0):
        result = super().invoke(request, schema_retry_index=schema_retry_index)
        situation = getattr(request, "situation_context", {}) or {}
        practice = situation.get("practice") if isinstance(situation, dict) else {}
        self.captured[-1]["practice"] = json.loads(
            json.dumps(practice if isinstance(practice, dict) else {},
                       ensure_ascii=False, default=str))
        return result


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import life_sharing_context as lsc
    import life_sharing_service as lss
    import result_method_behavior_smoke as harness
    import v05_result_sharing_continuation_smoke as sharing
    import v05_restart_continuation_smoke as restart

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("v05-same-result-loss")
    completed = False
    planner = None
    shutdown: dict = {}
    try:
        # -- 0) 真实作品 A：正式 Decision 写出一个版本；同一轮方法提案 TENTATIVE ---------
        seed = harness.seed_method_after_first_write(chain)
        receipt = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        result_version = sharing.head_version(chain, artifact_a)
        version_a = sharing.version(chain, artifact_a)
        handoff = seed.round_out.get("experience_handoff") or {}
        result_ref = str(handoff.get("progress_result_ref") or "")
        expected_result_ref = "artifact:%s@artifactversion:%s" % (
            bare(artifact_a), result_version)
        method_id = str(seed.method_id)
        method_ref = str(seed.method_ref)
        experience_id = str(seed.experience_id)
        created = seed.round_out.get("method_created") or {}
        experience = chain.cycle.practice.read_experience(experience_id) or {}
        check("the fixture's own formal round really wrote work A and proposed the method",
              bool(artifact_a) and version_a >= 2 and bool(result_version)
              and all(ok for _n, ok, _d in harness._CHECKS),
              {"artifact": work_ref(artifact_a), "version": result_version})
        check("the Decision's own proposal is saved by the real owner as TENTATIVE",
              created.get("method_state") == "TENTATIVE" and str(created.get("artifact_id"))
              == method_id, {"method": method_id, "state": created.get("method_state")})
        check("the receipt names the same A/R result identity the owner minted",
              result_ref == expected_result_ref and bool(receipt.get("version_id")),
              {"result_ref": result_ref, "expected": expected_result_ref,
               "receipt_version": str(receipt.get("version_id") or "")})
        check("Memory's own safe metadata anchors this experience to the same A/R",
              experience.get("artifact_ref") == work_ref(artifact_a)
              and experience.get("version_ref") == "artifactversion:%s" % result_version
              and bool(str(experience.get("owner_result") or "")),
              {"artifact_ref": experience.get("artifact_ref"),
               "version_ref": experience.get("version_ref"),
               "owner_result_ref": str(experience.get("owner_result") or "")[:96]})
        check("work A is still at the same head after the seed round",
              sharing.head_version(chain, artifact_a) == result_version
              and sharing.version(chain, artifact_a) == version_a)

        # -- 1) 正式 NO_ACTION + method_trial：只用来源对应 R 的真经历 → TRIAL ------------
        same_result_plan = type("SameResultPlan", (SameResultCognition, sharing.PlanCognition), {})
        planner = same_result_plan()
        chain.cognition.invoke = planner.invoke
        chain.cycle.work_artifact_id = artifact_a
        offered_method = chain.cycle.practice.read_method(method_id) or {}
        method_head_before = str(offered_method.get("head_version_id") or "")
        out_trial = sharing.run_round(chain, planner, {
            "op": "none",
            "script": {"decision": "NO_ACTION", "selected_candidate_id": None,
                       "deferred_candidate_ids": [],
                       "method_ref": method_ref,
                       "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                                        "detail": "这一次先写结论确实顺一些",
                                        "experience_ids": [experience_id]},
                       "short_rationale": "stub: evidenced trial on the same result"}},
            trigger="evt:v05-same-result-loss:trial")
        ROUNDS.append(meta(out_trial, {"round": "trial"}))
        stored = chain.cycle.practice.read_method(method_id) or {}
        trials = [dict(row) for row in (stored.get("trials") or [])
                  if isinstance(row, dict)]
        trial = trials[-1] if trials else {}
        check("the real owner records HELPED and moves the method to TRIAL",
              (out_trial.get("method_trial") or {}).get("outcome") == "HELPED"
              and stored.get("method_state") == "TRIAL" and len(trials) == 1,
              {"trial_outcome": (out_trial.get("method_trial") or {}).get("outcome"),
               "state": stored.get("method_state"), "trials": len(trials)})
        check("the trial cites exactly the experience that names A/R",
              [str(x) for x in (trial.get("experience_ids") or [])] == [experience_id],
              {"experience_ids": trial.get("experience_ids")})
        check("the trial's version_id is the evaluated method version, not the work version",
              bool(method_head_before)
              and str(trial.get("version_id") or "") == method_head_before
              and str(trial.get("version_id") or "") != result_version
              and str(stored.get("head_version_id") or "") != method_head_before,
              {"trial_version": trial.get("version_id"),
               "offered_method_head": method_head_before,
               "method_head_now": stored.get("head_version_id"),
               "work_version": result_version})
        check("work A still did not move while the method was trialled",
              sharing.head_version(chain, artifact_a) == result_version
              and sharing.version(chain, artifact_a) == version_a)

        # -- 2) 正式 prepare D：显式选择同一 A/R（本轮实际请求的 current_work 来源）--------
        service = chain.cycle.sharing_service
        check("the cycle is wired to the real sharing owner",
              isinstance(service, lss.LifeSharingService), type(service).__name__)
        out_prepare = sharing.run_round(
            chain, planner, {"op": "prepare", "text": D_TEXT, "title": "同一结果的来源损失草稿"},
            trigger="evt:v05-same-result-loss:prepare")
        ROUNDS.append(meta(out_prepare, {"round": "prepare-d"}))
        prepared = out_prepare.get("sharing") or {}
        draft_d = str(prepared.get("share_id") or "")
        draft_head = str(prepared.get("head_version_id") or "")

        def owner_read(include_text: bool = False) -> dict:
            return service.read_share(draft_d, include_text=include_text) if draft_d else {}

        def local_read() -> dict:
            return lsc.read_draft(service, draft_d) if draft_d else {}

        owner_d = service.read_share(draft_d, include_text=False) if draft_d else {}
        d_share = ((owner_d.get("data") or {}).get("share") or {})
        d_source = d_share.get("source") or {}
        d_artifact = str(d_source.get("artifact_id") or "")
        d_version = str(d_source.get("version_id") or "")
        draft_state = local_read()
        d_receipt = draft_state.get("receipt")
        offered_sources = [row for row in
                           (sharing.latest_sharing_block(planner).get("sources") or [])
                           if isinstance(row, dict)]
        check("prepare creates a DRAFT through the real owner from the offered current_work",
              prepared.get("ok") is True and prepared.get("state") == lss.STATE_DRAFT
              and bool(draft_d) and bare(draft_d) != bare(artifact_a)
              and any(str(row.get("kind") or "") == "current_work"
                      and bare(row.get("artifact_id")) == bare(artifact_a)
                      for row in offered_sources),
              {"draft": draft_d, "offered_sources": offered_sources})
        check("the draft's source is the SAME owner artifact A and version R",
              d_artifact == work_ref(artifact_a) and d_version == result_version
              and draft_state.get("source_current", {}).get("readable") is True,
              {"source_artifact": d_artifact, "source_version": d_version,
               "expected_artifact": work_ref(artifact_a), "expected_version": result_version})
        check("the draft is a separate artifact and the work head never moved",
              sharing.head_version(chain, artifact_a) == result_version
              and sharing.version(chain, artifact_a) == version_a)

        # -- 3) 健康基线：同一轮真实请求同时带 D / 方法行 / 显式评价，身份仍是 A/R ---------
        out_healthy = sharing.run_round(chain, planner, {"op": "none"},
                                        trigger="evt:v05-same-result-loss:healthy")
        ROUNDS.append(meta(out_healthy, {"round": "healthy-read"}))
        healthy_block = sharing.latest_sharing_block(planner)
        healthy_practice = json.loads(json.dumps(planner.captured[-1].get("practice") or {},
                                                 ensure_ascii=False, default=str))
        healthy_draft = (sharing.draft_rows(planner, draft_d) or [{}])[0]
        healthy_methods = [row for row in (healthy_practice.get("methods") or [])
                           if isinstance(row, dict)
                           and str(row.get("method_ref") or "") == method_ref]
        healthy_eval = (healthy_methods[0].get("evaluation") if healthy_methods else {}) or {}
        owner_d_now = owner_read()
        owner_d_source = ((owner_d_now.get("data") or {}).get("share") or {}).get(
            "source") or {}
        check("the healthy round's real request carries the open draft D",
              str(healthy_draft.get("share_id") or "") == draft_d
              and healthy_draft.get("state") == lss.STATE_DRAFT,
              {"draft": healthy_draft.get("share_id"), "state": healthy_draft.get("state")})
        check("the same request offers the method row with its explicit evaluation",
              bool(healthy_methods)
              and healthy_methods[0].get("method_state") == "TRIAL"
              and healthy_eval.get("outcome") == "HELPED"
              and healthy_eval.get("version_id") == method_head_before
              and int(healthy_eval.get("evidence_count") or 0) == 1,
              {"method_ref": (healthy_methods or [{}])[0].get("method_ref"),
               "state": (healthy_methods or [{}])[0].get("method_state"),
               "evaluation": healthy_eval})
        check("the healthy metadata still names the SAME A/R across owner/result/experience/draft",
              result_ref == expected_result_ref
              and owner_d_source.get("artifact_id") == work_ref(artifact_a)
              and owner_d_source.get("version_id") == result_version
              and (chain.cycle.practice.read_experience(experience_id) or {}).get(
                  "version_ref") == "artifactversion:%s" % result_version,
              {"result_ref": result_ref,
               "draft_source": {key: owner_d_source.get(key)
                                for key in ("artifact_id", "version_id")}})

        # -- 4) 只拒 A 来源读（故障注入，不是撤权）：正文扣留、方法域不被牵连 -----------
        # 注入放在 cycle 实际持有的同一个 sharing owner 上（动作路径与读路径都经由它）。
        original_projects = service.projects
        refusing = sharing.SourceRefusingPort(original_projects, source_id=artifact_a)
        service.projects = refusing
        check("the M0 experience stays readable while A's source read is refused",
              bool(chain.cycle.practice.read_experience(experience_id)),
              {"experience": experience_id})
        check("the practice owner still reports TRIAL before the governed round",
              (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL")

        out_governed = sharing.run_round(chain, planner, {"op": "none"},
                                         trigger="evt:v05-same-result-loss:governed-read")
        ROUNDS.append(meta(out_governed, {"round": "governed-read"}))
        governed_block = sharing.latest_sharing_block(planner)
        governed_practice = json.loads(json.dumps(planner.captured[-1].get("practice") or {},
                                                  ensure_ascii=False, default=str))
        governed_row = (sharing.draft_rows(planner, draft_d) or [{}])[0]
        governed_methods = [row for row in (governed_practice.get("methods") or [])
                            if isinstance(row, dict)
                            and str(row.get("method_ref") or "") == method_ref]
        governed_eval = (governed_methods[0].get("evaluation")
                         if governed_methods else {}) or {}
        owner_governed = owner_read(include_text=True)
        owner_governed_state = (owner_governed.get("data") or {}).get("share") or {}
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
        check("the owner's own read withholds too and never says 'not found'",
              owner_governed.get("ok") is True
              and (owner_governed.get("data") or {}).get("text_withheld") is True
              and SENTINEL not in json.dumps(owner_governed, ensure_ascii=False,
                                             default=str)
              and str(owner_governed.get("reason_code") or "") != "SOURCE_NOT_FOUND",
              {"state": owner_governed.get("state"),
               "reason_code": owner_governed.get("reason_code")})
        check("the method domain still offers the method at TRIAL with the owner's head",
              bool(governed_methods)
              and governed_methods[0].get("method_state") == "TRIAL"
              and governed_methods[0].get("head_version_id")
              == (chain.cycle.practice.read_method(method_id) or {}).get("head_version_id")
              and bool(chain.cycle.practice.read_experience(experience_id))
              and ("PRACTICE_METHOD_RETIRED"
                   not in list((governed_practice.get("withheld_methods") or {}).get(
                       "reason_codes") or [])),
              {"method_row": {key: (governed_methods or [{}])[0].get(key)
                              for key in ("method_ref", "head_version_id", "method_state")},
               "withheld": (governed_practice.get("withheld_methods") or {}).get(
                   "reason_codes")})
        check("the evaluation view is a real non-empty observation (facts or a named reason)",
              bool(governed_eval)
              and (governed_eval.get("available") is True
                   or bool(governed_eval.get("reason_code"))),
              {"evaluation": governed_eval})
        check("the governed round changed neither A/R nor the draft's recorded source",
              sharing.head_version(chain, artifact_a) == result_version
              and str(((owner_read().get("data") or {})
                       .get("share") or {}).get("source", {}).get("version_id") or "")
              == result_version
              and owner_governed_state.get("head_version_id") == draft_head,
              {"work_head": sharing.head_version(chain, artifact_a),
               "draft_head": owner_governed_state.get("head_version_id")})

        # -- 5) 显式 send_local：owner 拒、零写、sentinel 不泄漏 ----------------------
        out_refused = sharing.run_round(
            chain, planner, {"op": "send_local", "target": draft_d},
            trigger="evt:v05-same-result-loss:governed-send")
        ROUNDS.append(meta(out_refused, {"round": "governed-send"}))
        refused = out_refused.get("sharing") or {}
        after_refused = owner_read()
        refused_share = (after_refused.get("data") or {}).get("share") or {}
        check("the owner refuses the send with a named refusal and zero writes",
              refused.get("ok") is False and refused.get("delivered") is False
              and str(refused.get("status") or "") == lss.STATUS_STALE_SOURCE
              and str(refused.get("reason_code") or "") not in ("", "SOURCE_NOT_FOUND")
              and SENTINEL not in json.dumps(refused, ensure_ascii=False, default=str)
              and refused_share.get("head_version_id") == draft_head
              and refused_share.get("state") == lss.STATE_DRAFT
              and (after_refused.get("data") or {}).get("receipt") is None, refused)

        # -- 6) 故障解除：下一合法无 sharing 回合从 owner 重读，零自动动作 -------------
        service.projects = original_projects
        check("the temporary fault really exercised the source read port",
              refusing.refused_reads >= 1, {"refused_reads": refusing.refused_reads})
        receipts_before_recovery = (service.list_shares(include_text=False).get("data") or {})
        receipts_before_recovery = (receipts_before_recovery.get("counts") or {}).get("delivered")
        out_recover = sharing.run_round(chain, planner, {"op": "none"},
                                        trigger="evt:v05-same-result-loss:recovered")
        ROUNDS.append(meta(out_recover, {"round": "recovered-read"}))
        recovered_block = sharing.latest_sharing_block(planner)
        recovered_row = (sharing.draft_rows(planner, draft_d) or [{}])[0]
        recovered_state = local_read()
        receipts_after_recovery = (service.list_shares(include_text=False).get("data") or {})
        receipts_after_recovery = (receipts_after_recovery.get("counts") or {}).get("delivered")
        recovered_source = ((owner_read().get("data") or {}).get("share") or {}).get(
            "source") or {}
        check("after the injection ends the owner's readable text returns",
              recovered_row.get("text_available") is True
              and bool(recovered_row.get("text_excerpt"))
              and recovered_source.get("artifact_id") == work_ref(artifact_a)
              and recovered_source.get("version_id") == result_version,
              {"text_available": recovered_row.get("text_available"),
               "text_chars": recovered_row.get("text_chars"),
               "source": {key: recovered_source.get(key)
                          for key in ("artifact_id", "version_id")}})
        check("nothing was auto-sent, dropped or rebound by the recovery round",
              (out_recover.get("sharing") in (None, {}))
              and recovered_state.get("head_version_id") == draft_head
              and recovered_state.get("state") == lss.STATE_DRAFT
              and recovered_state.get("receipt") is None
              and d_receipt is None
              and receipts_after_recovery == receipts_before_recovery,
              {"state": recovered_state.get("state"),
               "head": recovered_state.get("head_version_id")})
        check("work A and the method domain are unchanged by the temporary fault",
              sharing.head_version(chain, artifact_a) == result_version
              and sharing.version(chain, artifact_a) == version_a
              and (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL"
              and bool(chain.cycle.practice.read_experience(experience_id)))

        # -- 7) 同一真实 owner 合法 archive A：只结束，不删版本；不 fallback 可续 B ------
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件", content=B_TEXT,
            grant_id=str(chain.data["artifact_grant_id"]), operation_key=CREATE_B_KEY)
        artifact_b = str(created_b.artifact_id or "")
        point_b = chain.port.resume_point(artifact_b) if artifact_b else None
        check("a second real work B exists and the owner reports it continuable",
              bool(artifact_b) and point_b is not None and point_b.resumable is True,
              {"artifact": work_ref(artifact_b),
               "lifecycle": getattr(point_b, "lifecycle", None)})
        chain.cycle.work_artifact_id = artifact_b
        version_b = sharing.version(chain, artifact_b)
        head_b = sharing.head_version(chain, artifact_b)
        point_a = chain.port.resume_point(artifact_a)
        archived = chain.port.archive_artifact(
            artifact_id=artifact_a, expected_revision=int(point_a.revision),
            grant_id=str(chain.data["artifact_grant_id"]), operation_key=ARCHIVE_KEY)
        after_a = chain.port.resume_point(artifact_a)
        check("the owner archived A as its own lifecycle verb",
              str(getattr(archived, "status", "")) in ("OK", "ok", "STATUS_OK")
              and str(getattr(archived, "effect_state", "")) in ("APPLIED", "EffectState.APPLIED")
              and after_a is not None and after_a.lifecycle == "ARCHIVED",
              {"status": getattr(archived, "status", None),
               "effect": str(getattr(archived, "effect_state", None)),
               "lifecycle": getattr(after_a, "lifecycle", None)})
        check("...the versions, head and words of A are kept (archive is not a delete)",
              after_a is not None and after_a.head_version_id == result_version
              and sharing.version(chain, artifact_a) == version_a
              and bool(after_a.content))

        stamp = sharing.next_instant()
        candidate_a = sharing.continue_candidate(chain, artifact_a, observed_at=stamp)
        refusal = ""
        out_refuse: dict = {}
        try:
            if candidate_a:
                planner.plan = [{"script": {
                    "decision": "CONTINUE", "selected_candidate_id": candidate_a,
                    "short_rationale": "stub: continue the work named by the activity"}}]
            else:
                planner.plan = [{"script": {
                    "decision": "CONTINUE", "selected_candidate_id": "cand:v05-same-result-loss:none",
                    "short_rationale": "stub: illegal answer naming A"}}]
            out_refuse = chain.cycle.evaluate_once(
                trigger_ref="evt:v05-same-result-loss:refused-continue", act=True,
                observed_at=stamp)
        except Exception as exc:  # noqa: BLE001 - the named refusal is the evidence
            refusal = "%s: %s" % (type(exc).__name__, exc)
        out_refuse = out_refuse if isinstance(out_refuse, dict) else {}
        ROUNDS.append(meta(out_refuse, {"round": "archived-continue",
                                        "continue_candidate_for_a": bool(candidate_a),
                                        "exception": refusal[:200]}))
        refusal_text = refusal or json.dumps(
            {key: out_refuse.get(key) for key in
             ("decision", "outcome", "detail", "practice_error", "decision_error")},
            ensure_ascii=False, default=str)
        check("the archived work A is not continued: the round refuses by name",
              "WORK_NOT_CONTINUABLE" in refusal_text
              or "does not exist in CandidateSet" in refusal_text
              or "CANDIDATE" in refusal_text.upper(),
              {"candidate_a": bool(candidate_a), "refusal": refusal_text[:300]})
        check("...and nothing was written: no version, receipt or new experience, no fallback B",
              sharing.head_version(chain, artifact_a) == result_version
              and sharing.version(chain, artifact_a) == version_a
              and sharing.head_version(chain, artifact_b) == head_b
              and sharing.version(chain, artifact_b) == version_b
              and not (out_refuse.get("step_receipt") or {})
              and not (out_refuse.get("experience_handoff") or {})
              and len(chain.cycle.practice.experiences()) == 1,
              {"a": [sharing.version(chain, artifact_a), result_version],
               "b": [sharing.version(chain, artifact_b), version_b],
               "receipt": bool(out_refuse.get("step_receipt")),
               "handoff": bool(out_refuse.get("experience_handoff"))})

        # -- 8) D 的来源 ARCHIVED 仍可读；send_local 由 owner 拒且零写 -----------------
        owner_archived = owner_read()
        archived_source = ((owner_archived.get("data") or {}).get("source_current") or {})
        archived_draft = local_read()
        check("the archived source stays readable and honestly marked ARCHIVED",
              archived_source.get("state") == lss.SOURCE_STATE_ARCHIVED
              and archived_source.get("readable") is True
              and archived_source.get("stale") is True
              and archived_source.get("reason_code") == lss.REASON_SOURCE_ARCHIVED
              and archived_draft.get("text_available") is True,
              {"owner_state": archived_source.get("state"),
               "projected": archived_draft.get("source_current")})
        check("the draft still records the SAME A/R it was prepared from",
              archived_draft.get("source_current", {}).get("matches_recorded") is False
              and str(((owner_read().get("data") or {})
                       .get("share") or {}).get("source", {}).get("artifact_id") or "")
              == work_ref(artifact_a),
              {"source_current": archived_draft.get("source_current")})
        out_archived_send = sharing.run_round(
            chain, planner, {"op": "send_local", "target": draft_d},
            trigger="evt:v05-same-result-loss:archived-send")
        ROUNDS.append(meta(out_archived_send, {"round": "archived-send"}))
        refused_archived = out_archived_send.get("sharing") or {}
        after_archived_send = local_read()
        check("the owner refuses the draft bound to the archived source",
              refused_archived.get("ok") is False
              and str(refused_archived.get("status") or "") == lss.STATUS_STALE_SOURCE
              and str(refused_archived.get("reason_code") or "") == lss.REASON_SOURCE_ARCHIVED
              and not refused_archived.get("delivered"), refused_archived)
        check("...with zero writes to the draft, its head, receipts or the work",
              after_archived_send.get("head_version_id") == draft_head
              and after_archived_send.get("state") == lss.STATE_DRAFT
              and after_archived_send.get("receipt") is None
              and sharing.head_version(chain, artifact_a) == result_version
              and sharing.head_version(chain, artifact_b) == head_b)
        check("the archived work source never auto-retires the method evidence domain",
              (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL"
              and bool(chain.cycle.practice.read_experience(experience_id))
              and (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              != "RETIRED")

        # -- 9) 最后合法无 sharing 的 NO_ACTION：零写、不强制切事/退休/复活 --------------
        heads_before_quiet = (sharing.head_version(chain, artifact_a), head_b, draft_head)
        receipts_before_quiet = (service.list_shares(include_text=False).get("data")
                                 or {}).get("counts", {}).get("delivered")
        out_quiet = sharing.run_round(chain, planner, {"op": "none"},
                                      trigger="evt:v05-same-result-loss:quiet")
        ROUNDS.append(meta(out_quiet, {"round": "quiet"}))
        after_quiet = local_read()
        receipts_after_quiet = (service.list_shares(include_text=False).get("data")
                                or {}).get("counts", {}).get("delivered")
        check("a plain NO_ACTION round takes no sharing action",
              not (out_quiet.get("sharing") or {}), out_quiet.get("sharing"))
        check("nothing is auto-dropped, rebound, sent, retargeted or resurrected",
              (sharing.head_version(chain, artifact_a),
               sharing.head_version(chain, artifact_b), after_quiet.get("head_version_id"))
              == heads_before_quiet
              and after_quiet.get("state") == lss.STATE_DRAFT
              and receipts_after_quiet == receipts_before_quiet
              and sharing.version(chain, artifact_a) == version_a
              and sharing.version(chain, artifact_b) == version_b
              and (chain.cycle.practice.read_method(method_id) or {}).get("method_state")
              == "TRIAL")

        TABLE.append({
            "work_artifact": work_ref(artifact_a),
            "result_version": result_version,
            "result_ref": result_ref,
            "experience": {"experience_id": experience_id,
                           "artifact_ref": experience.get("artifact_ref"),
                           "version_ref": experience.get("version_ref")},
            "method": {"artifact_id": method_id, "method_ref": method_ref,
                       "trial_version_id": trial.get("version_id"),
                       "state_after": (chain.cycle.practice.read_method(method_id) or {}).get(
                           "method_state")},
            "draft": {"share_id": draft_d, "source_artifact": d_artifact,
                      "source_version": d_version, "state": after_quiet.get("state")},
            "governed": {"method_row_state": (governed_methods or [{}])[0].get("method_state"),
                         "evaluation": governed_eval.get("available", governed_eval.get(
                             "reason_code")),
                         "source_refusals": refusing.refused_reads},
            "archived": {"lifecycle_a": getattr(after_a, "lifecycle", None),
                         "a_version_count": sharing.version(chain, artifact_a),
                         "b_version_count": sharing.version(chain, artifact_b)},
        })
        completed = True
        return 0 if all(ok for _name, ok, _detail in CHECKS) else 1
    finally:
        try:
            if not shutdown:
                shutdown = restart._stop_services(chain.services)
        except Exception as exc:  # noqa: BLE001 - a stop failure is printed, not hidden
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        try:
            chain.runtime.close()
        except Exception as exc:  # noqa: BLE001
            print("  runtime close error:", type(exc).__name__, str(exc)[:200])
        print("\nIDENTITY TABLE (safe refs only)")
        for entry in TABLE:
            print(json.dumps(entry, ensure_ascii=False, default=str))
        print("\nROUNDS (decision metadata only)")
        for entry in ROUNDS:
            print(json.dumps(entry, ensure_ascii=False, default=str))
        print("\nSHUTDOWN", json.dumps(shutdown, ensure_ascii=False, default=str)[:300])
        failed = [name for name, ok, _detail in CHECKS if not ok]
        print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
        for name in failed:
            print("FAILED:", name)
        if not completed:
            print("RESULT: ERROR (chain aborted before the end)")
        else:
            print("RESULT: %s" % ("FAIL" if failed else "PASS"))


if __name__ == "__main__":
    sys.exit(main())
