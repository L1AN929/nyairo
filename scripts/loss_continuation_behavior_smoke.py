#!/usr/bin/env python3
"""零模型真实 owner 冒烟：退休之后，下一轮真的不能再命名那条做法。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自己的
   Unix socket，落盘）、真实 M0 Memory、真实 ProductionLifeRuntime、B 的实践工作台、
   AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、AR-0 受管作品适配器。决策与内容回答
   是脚本替身（文件里明示），不宣称真实模型自然采用。
2. 第一轮由正式 Decision 的 ``method_proposal`` 经 AG-1 提交、cycle 自己通过真实 owner
   存成一条 TENTATIVE 做法。
3. 基线：正式 ``evaluate_once`` 的脚本回答命名该做法，AG-1 用本轮自己的允许清单接受它，
   Decision 提交、前台 Activity 由 owner 真正放弃（``ABANDONED``）。
4. 损失：沿 owner 既有 ``retire_method`` 写一个 RETIRED 版本（CAS，不删文档）。
5. 下一轮：正式 ``evaluate_once`` 的脚本回答仍然命名旧 ``method_ref``。本轮给 AG-1 的
   允许清单由 ``practice_method_refs()`` 现读、已不含该手柄，所以这次不是直接调用
   validator，而是真走 AG-1 之后被 ``INVENTED_METHOD_REF`` 拒绝：没有 Decision 被提交。
   模型 view 的 ``practice`` 用 ``withheld_methods`` 具名 ``PRACTICE_METHOD_RETIRED``；
   owner 仍能读出正文与版本；试用 / 修订按同一 owner 规则拒绝；它仍被当作"方法文档"
   而不是可续写作品；写作视图不再携带退休指令。
6. 同一状态下再一次不引用旧手柄的合法 Decision 经同一条正式链正常提交（``NO_ACTION``）。
7. 所有自有 runtime / 服务在 finally 关闭；夹具根保留便于复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/loss_continuation_behavior_smoke.py
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
FIXTURE_ROOT = Path(os.environ.get("NYAIRO_LOSS_CONTINUATION_FIXTURE_ROOT")
                    or "/tmp/nyairo-loss-continuation-fixtures")

_CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    _CHECKS.append((name, bool(condition), detail))
    print("CHECK %-62s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def bare_id(value) -> str:
    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def scripted_round(chain, raw: dict, *, trigger_ref: str):
    """Drive one real ``evaluate_once`` round with a scripted raw model answer.

    Only the cognition adapter's answer is a script (the file says so); AG-1's validation,
    AG-2 adoption, the decision commit and every owner call stay real.  The allow-list is
    *not* passed in here -- the cycle reads it from the practice workspace itself.
    """

    chain.cognition.script = dict(raw)
    calls_before = chain.cognition.call_count
    out = chain.cycle.evaluate_once(trigger_ref=trigger_ref, act=True)
    return out, chain.cognition.call_count - calls_before


def run() -> None:
    if os.name != "posix":
        raise SystemExit("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")

    import agency_decision_ag1 as ag1
    import result_method_behavior_smoke as harness

    harness.FIXTURE_ROOT = FIXTURE_ROOT
    print("fixture root:", FIXTURE_ROOT)
    chain = harness.build_chain("loss")
    try:
        method = harness.seed_method_after_first_write(chain)
        check("seeding chain's own real-owner checks pass",
              all(ok for _name, ok, _detail in harness._CHECKS),
              [name for name, ok, _detail in harness._CHECKS if not ok])
        method_id = str(method.method_id)
        ref = str(method.method_ref)

        # -- 基线：正式一轮命名该做法，AG-1 用本轮允许清单接受并提交 -------------------
        baseline, baseline_calls = scripted_round(chain, {
            "decision": "ABANDON", "selected_candidate_id": None,
            "deferred_candidate_ids": [], "method_ref": ref,
            "short_rationale": "stub: baseline round naming the method"}, trigger_ref="evt:loss:baseline")
        baseline_usage = baseline.get("method_usage") or {}
        check("baseline formal round really ran the decision model", baseline_calls >= 1,
              {"llm_calls": baseline_calls})
        check("baseline formal round accepts the offered handle and commits",
              baseline.get("outcome") == "ABANDONED"
              and baseline.get("decision") == "ABANDON"
              and baseline_usage.get("method_ref") == ref,
              {"outcome": baseline.get("outcome"), "decision": baseline.get("decision"),
               "method_usage": baseline_usage})

        # -- 损失：沿 owner 既有入口退休，CAS 写一个新版本 ---------------------------
        head_before = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        retired = chain.cycle.practice.service.retire_method(
            method_id, expected_head=head_before,
            operation_key="practice:loss-smoke:retire")
        check("the real owner really wrote a RETIRED version",
              bool(retired.get("ok")) and (retired.get("data") or {}).get("method_state")
              == "RETIRED", {"ok": retired.get("ok"), "status": retired.get("status")})
        check("retirement kept the document (no physical delete)",
              bool((retired.get("data") or {}).get("content_kept")))

        # -- 下一轮：模型 view 具名损失，正式链以 INVENTED_METHOD_REF 拒绝旧手柄 ------
        after = chain.cycle.decision_situation_context(now=TS)
        practice = after.get("practice") or {}
        after_refs = list(practice.get("method_refs") or [])
        withheld = practice.get("withheld_methods") or {}
        check("the model view no longer offers the retired method", ref not in after_refs,
              {"offered": len(after_refs)})
        check("the model view names the loss instead of pretending it never existed",
              int(withheld.get("count") or 0) >= 1
              and "PRACTICE_METHOD_RETIRED" in (withheld.get("reason_codes") or []),
              {"count": withheld.get("count"), "reasons": withheld.get("reason_codes")})
        check("cycle no longer hands the ref to AG-1",
              ref not in chain.cycle.practice_method_refs())

        refused, refused_calls = scripted_round(chain, {
            "decision": "NO_ACTION", "selected_candidate_id": None,
            "reason_codes": ["NO_ACTION_CHOSEN"], "deferred_candidate_ids": [],
            "method_ref": ref,
            "short_rationale": "stub: still naming the retired method"},
            trigger_ref="evt:loss:retired-ref")
        check("the retired handle is refused by the formal round with INVENTED_METHOD_REF",
              refused.get("outcome") == "DECISION_FAILED"
              and refused.get("detail") == ag1.FAILURE_CLASS_INVENTED_METHOD_REF,
              {"outcome": refused.get("outcome"), "detail": refused.get("detail")})
        check("the refusal really reached the decision model", refused_calls >= 1,
              {"llm_calls": refused_calls})
        check("no Decision was committed for the refused round",
              refused.get("decision") is None and refused.get("decision_route") is None,
              {"decision": refused.get("decision"),
               "decision_route": refused.get("decision_route")})

        # -- 同一状态：不引用旧手柄的合法 Decision 仍能走完整条正式链 ------------------
        legal, legal_calls = scripted_round(chain, {
            "decision": "NO_ACTION", "selected_candidate_id": None,
            "reason_codes": ["NO_ACTION_CHOSEN"], "deferred_candidate_ids": [],
            "short_rationale": "stub: legal quiet round"}, trigger_ref="evt:loss:legal")
        check("a legal Decision that does not name the retired handle still commits",
              legal.get("outcome") == "NO_ACTION" and legal.get("decision") == "NO_ACTION"
              and legal.get("decision_route") == ag1.ROUTE_COGNITIVE_REQUIRED,
              {"outcome": legal.get("outcome"), "decision": legal.get("decision"),
               "route": legal.get("decision_route")})
        check("the legal round really reached the decision model", legal_calls >= 1,
              {"llm_calls": legal_calls})

        # -- owner 的文档与规则都还在：退休是状态，不是删除 ---------------------------
        stored = chain.cycle.practice.read_method(method_id)
        check("the retired document is still readable at its new head",
              bool(stored.get("head_version_id")) and stored.get("head_version_id") != head_before
              and bool(str(stored.get("excerpt") or "").strip()),
              {"state": stored.get("method_state")})
        trial = chain.cycle.practice.service.record_trial(
            method_id, outcome="UNKNOWN", detail="", experience_ids=[],
            expected_head=str(stored["head_version_id"]),
            operation_key="practice:loss-smoke:trial")
        check("a later trial is refused by the owner's own RETIRED rule",
              trial.get("reason_code") == "PRACTICE_METHOD_RETIRED",
              {"reason_code": trial.get("reason_code")})
        revision = chain.cycle.practice.service.revise_method(
            method_id, instruction="stub: revised", expected_head=str(stored["head_version_id"]),
            operation_key="practice:loss-smoke:revise")
        check("a later revision is refused by the same rule",
              revision.get("reason_code") == "PRACTICE_METHOD_RETIRED",
              {"reason_code": revision.get("reason_code")})

        # 仍然是方法文档：不能因为退休就掉进"可续写的作品"。
        method_ids = [bare_id(item) for item in chain.cycle._practice_method_ids()]
        check("the retired method is still excluded from work offers",
              bare_id(method_id) in method_ids)

        # 写作视图也不再把它当参考，更不把退休做法的指令送进 writer（本夹具只有这一条方法）。
        writing = chain.cycle.practice_writing_context("")
        writing_blob = json.dumps(writing, ensure_ascii=False) if writing else ""
        writing_refs = [str(row.get("method_ref") or "")
                        for row in (writing or {}).get("methods") or []]
        check("the writing view no longer carries the retired method",
              ref not in writing_refs, {"methods": len(writing_refs)})
        check("the writer view carries no retired instruction",
              harness.METHOD_INSTRUCTION not in writing_blob,
              {"writing": writing_blob[:300]})

        # 读两次上下文不写任何东西：owner 的 revision / head / trials 不变。
        stable_before = chain.cycle.practice.read_method(method_id)
        chain.cycle.decision_situation_context(now=TS)
        chain.cycle.practice_writing_context("")
        stable_after = chain.cycle.practice.read_method(method_id)
        check("reading the loss twice writes nothing",
              (stable_before.get("revision"), stable_before.get("head_version_id"),
               len(stable_before.get("trials") or []))
              == (stable_after.get("revision"), stable_after.get("head_version_id"),
                  len(stable_after.get("trials") or [])),
              {"before": {"revision": stable_before.get("revision")},
               "after": {"revision": stable_after.get("revision")}})
    finally:
        harness.close_chain(chain)


def main() -> int:
    run()
    failed = [name for name, ok, _detail in _CHECKS if not ok]
    print("\nchecks: %d, failed: %d" % (len(_CHECKS), len(failed)))
    if failed:
        for name in failed:
            print("FAILED:", name)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
