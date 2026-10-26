#!/usr/bin/env python3
"""v0.5 零模型真实 owner 冒烟：来源归档后怎样继续（不被偷换、旧草稿拒发）。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发。复用
``result_method_behavior_smoke`` 的隔离装配与 ``v05_result_sharing_continuation_smoke``
的正式轮助手（``PlanCognition`` / ``run_round`` / ``continue_candidate``）：

1. 真实作品 A 的一个版本由正式 Decision 写出（种子轮），得到 owner 结果身份。
2. 正式 Decision 从 A 的当前 head prepare 草稿 D：D 是另一件 artifact，精确绑定 A 的那一版。
3. 另建可续作品 B（真实 owner ``create_artifact``），并把既有轮选/绑定（``work_artifact_id``）
   指 B —— B 是可继续的合法对象。
4. 同一真实 owner 合法 ``archive_artifact`` A：只改生命周期与 revision，版本/head/正文保留。
5. 下一正式请求仍由当前 Activity 点名 A：若仍给出 CONTINUE 候选，选它经既有
   ``WORK_NOT_CONTINUABLE`` 具名拒绝；若该候选已被撤掉，用最终实际请求的合法集证明，
   并让 A 的非法回答被 AG-1 拒。两种情况都不写 A/B 版本、不产生执行 receipt/新 handoff，
   也不 fallback 到 B。
6. D 的来源现为 ARCHIVED 仍可读（不是 deleted/withheld）；正式 NO_ACTION 附 send_local D
   由 owner 拒 ``SOURCE_ARCHIVED``，D 的 head/state/receipt 与 A 一字未变。
7. 下一无 sharing 的合法 NO_ACTION 零写：不自动 drop/rebind/send，不强制放弃或切 B。

Decision 与内容回答是脚本替身（明示），不宣称真模型自然采用、用户收到、G1 或 24h。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_source_loss_continuation_smoke.py
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

D_TEXT = "这一段来自作品归档前的那一版，先留在这里。"
B_TEXT = "另一件还没结束的作品，本来可以继续。"
ARCHIVE_KEY = "op:v05-source-loss:archive-a"
CREATE_B_KEY = "op:v05-source-loss:create-b"

CHECKS: list[tuple[str, bool, object]] = []
ROUNDS: list[dict] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-70s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def meta(out: dict, extra: dict | None = None) -> dict:
    """Safe structural row: no private text, no prompt body."""

    sharing = out.get("sharing") if isinstance(out.get("sharing"), dict) else {}
    row = {
        "decision": str(out.get("decision"))[:48],
        "outcome": str(out.get("outcome") or "")[:48],
        "sharing_ok": sharing.get("ok"),
        "sharing_status": str(sharing.get("status") or "")[:32],
        "sharing_reason": str(sharing.get("reason_code") or "")[:48],
        "error": str(out.get("detail") or out.get("practice_error") or "")[:160],
    }
    row.update(extra or {})
    return row


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
    chain = harness.build_chain("v05-source-loss")
    completed = False
    planner = None
    shutdown: dict = {}
    try:
        # -- 0) 真实作品 A：正式 Decision 写出一个版本 --------------------------------
        seed = harness.seed_method_after_first_write(chain)
        receipt = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        head_a = sharing.head_version(chain, artifact_a)
        version_a = sharing.version(chain, artifact_a)
        check("a formal Decision really wrote work A through the owner",
              bool(artifact_a) and version_a >= 2 and bool(head_a),
              {"artifact": artifact_a, "version": version_a})
        service = chain.cycle.sharing_service
        check("the cycle is wired to the real sharing owner",
              isinstance(service, lss.LifeSharingService), type(service).__name__)
        experiences_before = len(chain.cycle.practice.experiences())

        # -- 1) 正式 Decision 从 A 的当前 head prepare D ------------------------------
        chain.cycle.work_artifact_id = artifact_a
        planner = sharing.install_planner(chain)
        out_prepare = sharing.run_round(
            chain, planner, {"op": "prepare", "text": D_TEXT, "title": "归档前那一版"},
            trigger="evt:v05-source-loss:prepare")
        sharing.record_round("prepare-d", out_prepare, planner)
        first = out_prepare.get("sharing") or {}
        d = str(first.get("share_id") or "")
        d_head = str(first.get("head_version_id") or "")
        owner_d = service.read_share(d, include_text=False) if d else {}
        d_bound = str(((owner_d.get("data") or {}).get("share") or {}).get(
            "source", {}).get("version_id") or "")
        check("prepare creates a DRAFT bound to A's current version",
              first.get("ok") is True and first.get("state") == lss.STATE_DRAFT
              and d_bound == head_a and sharing.head_version(chain, artifact_a) == head_a,
              {"draft": d, "bound": d_bound, "head": head_a})

        # -- 2) 另建可续 B，并把既有轮选/绑定指 B（正规 owner 写入 + 既有配置入口）-----
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件", content=B_TEXT,
            grant_id=str(chain.data["artifact_grant_id"]), operation_key=CREATE_B_KEY)
        artifact_b = str(created_b.artifact_id or "")
        point_b = chain.port.resume_point(artifact_b) if artifact_b else None
        check("a second real work B exists and the owner reports it continuable",
              bool(artifact_b) and point_b is not None and point_b.resumable is True,
              {"artifact": artifact_b, "lifecycle": getattr(point_b, "lifecycle", None)})
        chain.cycle.work_artifact_id = artifact_b
        version_b = sharing.version(chain, artifact_b)
        head_b = sharing.head_version(chain, artifact_b)
        check("the existing round selection is bound to B",
              str(chain.cycle.work_artifact_id) == artifact_b, chain.cycle.work_artifact_id)

        # -- 3) 同一真实 owner 合法 archive A：只结束，不删版本 ------------------------
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
        check("...the versions, head and words are kept (archive is not a delete)",
              after_a is not None and after_a.head_version_id == head_a
              and sharing.version(chain, artifact_a) == version_a
              and bool(after_a.content))

        # -- 4) 下一正式请求：仍由当前 Activity 点名 A，不 fallback B ------------------
        stamp = sharing.next_instant()
        candidate_a = sharing.continue_candidate(chain, artifact_a, observed_at=stamp)
        refusal = ""
        out_refuse = {}
        try:
            if candidate_a:
                planner.plan = [{"script": {
                    "decision": "CONTINUE", "selected_candidate_id": candidate_a,
                    "short_rationale": "stub: continue the work named by the activity"}}]
            else:
                # 该候选已被 readiness 撤掉时，用 A 的陈旧/不存在候选作答：AG-1 必须拒。
                planner.plan = [{"script": {
                    "decision": "CONTINUE", "selected_candidate_id": "cand:v05-source-loss:none",
                    "short_rationale": "stub: illegal answer naming A"}}]
            out_refuse = chain.cycle.evaluate_once(
                trigger_ref="evt:v05-source-loss:refused-continue", act=True, observed_at=stamp)
        except Exception as exc:  # noqa: BLE001 - the named refusal is the evidence
            refusal = "%s: %s" % (type(exc).__name__, exc)
        out_refuse = out_refuse if isinstance(out_refuse, dict) else {}
        ROUNDS.append(meta(out_refuse, {"continue_candidate_for_a": bool(candidate_a),
                                        "exception": refusal[:200]}))
        refusal_text = refusal or json.dumps(
            {key: out_refuse.get(key) for key in
             ("decision", "outcome", "detail", "practice_error", "decision_error")},
            ensure_ascii=False, default=str)
        heads_after = (sharing.head_version(chain, artifact_a),
                       sharing.head_version(chain, artifact_b))
        check("...the archived work A keeps its head", sharing.head_version(chain, artifact_a) == head_a)
        check("the archived work A is not continued: the round refuses by name",
              "WORK_NOT_CONTINUABLE" in refusal_text
              or "does not exist in CandidateSet" in refusal_text
              or "CANDIDATE" in refusal_text.upper(),
              {"candidate_a": bool(candidate_a), "refusal": refusal_text[:300]})
        check("...and nothing was written: no version, receipt or new handoff, no fallback B",
              heads_after == (head_a, head_b)
              and sharing.version(chain, artifact_a) == version_a
              and sharing.version(chain, artifact_b) == version_b
              and not (out_refuse.get("step_receipt") or {})
              and not (out_refuse.get("experience_handoff") or {})
              and len(chain.cycle.practice.experiences()) == experiences_before,
              {"heads": heads_after, "versions": [sharing.version(chain, artifact_a),
                                                  sharing.version(chain, artifact_b)]})

        # -- 5) D 的来源 ARCHIVED 仍可读；send_local 由 owner 拒且零写 -----------------
        draft_view = lsc.read_draft(service, d)
        source_now = draft_view.get("source_current") or {}
        owner_now = service.read_share(d, include_text=False)
        owner_source = ((owner_now.get("data") or {}).get("source_current") or {})
        check("the archived source stays readable and honestly marked ARCHIVED",
              owner_source.get("state") == lss.SOURCE_STATE_ARCHIVED
              and owner_source.get("readable") is True
              and source_now.get("readable") is True
              and source_now.get("stale") is True
              and source_now.get("reason_code") == lss.REASON_SOURCE_ARCHIVED
              and draft_view.get("text_available") is True,
              {"owner_state": owner_source.get("state"), "projected": source_now})
        out_send = sharing.run_round(chain, planner, {"op": "send_local", "target": d},
                                     trigger="evt:v05-source-loss:archived-send")
        sharing.record_round("archived-send-d", out_send, planner)
        refused_send = out_send.get("sharing") or {}
        after_d = lsc.read_draft(service, d)
        check("the owner refuses the draft bound to the archived source",
              refused_send.get("ok") is False
              and str(refused_send.get("status") or "") == lss.STATUS_STALE_SOURCE
              and str(refused_send.get("reason_code") or "") == lss.REASON_SOURCE_ARCHIVED
              and not refused_send.get("delivered"), refused_send)
        check("...with zero writes to the draft, its head, receipts or the work",
              after_d.get("head_version_id") == d_head
              and after_d.get("state") == lss.STATE_DRAFT
              and after_d.get("receipt") is None
              and sharing.head_version(chain, artifact_a) == head_a)

        # -- 6) 下一无 sharing 的合法 NO_ACTION：零写、不强制放弃或切 B ----------------
        heads_before_quiet = (sharing.head_version(chain, artifact_a), head_b, d_head)
        receipts_before = (service.list_shares(include_text=False).get("data") or {}).get(
            "counts", {}).get("delivered")
        out_quiet = sharing.run_round(chain, planner, {"op": "none"},
                                      trigger="evt:v05-source-loss:quiet")
        sharing.record_round("quiet", out_quiet, planner)
        after_quiet = lsc.read_draft(service, d)
        receipts_after = (service.list_shares(include_text=False).get("data") or {}).get(
            "counts", {}).get("delivered")
        check("a plain NO_ACTION round takes no sharing action",
              not (out_quiet.get("sharing") or {}), out_quiet.get("sharing"))
        check("nothing is auto-dropped, rebound, sent or retargeted to B",
              (sharing.head_version(chain, artifact_a),
               sharing.head_version(chain, artifact_b), after_quiet.get("head_version_id"))
              == heads_before_quiet
              and after_quiet.get("state") == lss.STATE_DRAFT
              and receipts_after == receipts_before
              and sharing.version(chain, artifact_b) == version_b)

        completed = True
        return 0 if all(ok for _name, ok, _detail in CHECKS) else 1
    finally:
        try:
            if not shutdown:
                shutdown = restart._stop_services(chain.services)
        except Exception as exc:  # noqa: BLE001
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        try:
            chain.runtime.close()
        except Exception as exc:  # noqa: BLE001
            print("  runtime close error:", type(exc).__name__, str(exc)[:200])
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
