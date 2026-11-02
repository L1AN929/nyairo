#!/usr/bin/env python3
"""G2 A 路线第三条接缝：同一轮里 RESUME 挂起作品 H / START 另一件 / NO_ACTION 是否都真合法。

只回答一件事：**在不改任何生产规则的前提下**，H（普通作品续写，G2 离线样本的实际类型）
被显式条件 PAUSE 并释放前台、且该条件由正式 LR-4 判为满足之后，同一个冻结结构里
H 的 RESUME、另一件不同作品的 START 与 NO_ACTION 是否都进入最终合法集。

边界（写死在这里，不靠调用方自觉）：

* 离线：新 ``mkdtemp`` scratch、真 Supply owner、正式 runtime/runner/AG-1/AG-2/LR-2/LR-4、
  现成 ``history_choice_natural_v2.freeze_and_check`` 结构门；``http.client`` 连接层硬断。
* Decision 与内容是**脚本替身**（走正式 AG-1 校验与 AG-2 采纳），它只抄本轮 prompt 里
  真实给出的候选 id 与 ``pause_condition.condition_ref``，不手填合法集、不手造 OPEN、
  不直接调 resolver。
* 时间推进是显式 ``observed_at`` 夹具（+时限），**不是自然经过**；PAUSE 指向的时刻由
  脚本 Decision 自己命名（``later_item.park_current``），没有默认 timer。
* 不读任何已有 root、不 source 密钥、不发网络、不占产品预算；scratch 全部保留不清理。

输出只有固定 reason code / 枚举 / ID（含不透明 ref）/ 计数 / 布尔：
预算只投影 ``run_window_calls/utc_day_calls/run_window_max/utc_day_max/state``
（不输出 ``recent_tokens``、账本路径、历史或 windows），异常只留 type 名与固定 code；
不含 prompt、正文、环境变量或密钥。``cleanup`` 只记录关闭调用结果，不宣称已 drain。
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1

PARK_MINUTES = 5           # 预登记时限的最小合法值（life_future_items.MIN_DELAY_MINUTES）
ADVANCE_MARGIN_MINUTES = 6  # 夹具时钟：越过 Park 时刻（不是自然经过）
PAUSE_REASON = "TIMING_NOT_APPROPRIATE"

#: 预算只投影这五项；``view()`` 还带 path/history/windows/recent_tokens，一律不输出。
_BUDGET_FIELDS = ("run_window_calls", "utc_day_calls",
                  "run_window_max", "utc_day_max", "state")
_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]*")


def _code(value: Any, fallback: str) -> str:
    """只保留前导 UPPER_SNAKE code；任意自由文本一律换成固定 fallback。"""

    match = _CODE_RE.match(str(value or ""))
    return match.group(0) if match else fallback


def _budget_view(budget: Any) -> dict[str, Any]:
    """严格白名单：只出调用数/上限/state，不含 token 明细、账本路径或历史。"""

    view = getattr(budget, "view", None)
    raw = view() if callable(view) else {}
    if not isinstance(raw, Mapping):
        return {key: None for key in _BUDGET_FIELDS}
    return {key: raw.get(key) for key in _BUDGET_FIELDS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _after(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(
        timespec="milliseconds")


class DualChoiceTransport(v2.CountingTransport):
    """脚本 Decision 替身：START 第一件可执行 artifact → PAUSE 并抄本轮 offer 的 park 条件。

    每次只读**真实渲染出来的** payload，按 AG-1 给它的候选与合法枚举作答；不构造合法集，
    也不猜条件 ref —— 条件 ref 直接抄本轮 ``situation_context.pause_condition.condition_ref``。
    """

    def __init__(self) -> None:
        super().__init__("{}", label="dual-choice")
        self.stage = 0
        self.answers: list[dict[str, Any]] = []
        self.offered_pause: dict[str, Any] = {}

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        try:
            payload = json.loads(user_prompt)
        except ValueError:
            payload = {}
        answer: dict[str, Any] = {"decision": "NO_ACTION",
                                  "reason_codes": ["NO_ACTION_CHOSEN"]}
        if self.stage == 0:
            startable = [
                row for row in (payload.get("candidates") or [])
                if isinstance(row, Mapping)
                and {"START", "CONTINUE", "RESUME"} & set(row.get("legal_decisions") or ())
                and any(str(ref).startswith("artifact:") for ref in (row.get("target_refs") or ()))]
            if startable:
                self.stage = 1
                answer = {"decision": "START",
                          "selected_candidate_id": startable[0]["candidate_id"],
                          "reason_codes": ["OPEN_ENDED_CONTINUATION"]}
        elif self.stage == 1:
            block = ((payload.get("situation_context") or {}).get("pause_condition") or {})
            if isinstance(block, Mapping) and block.get("available") and block.get("condition_ref"):
                self.stage = 2
                self.offered_pause = {
                    "source": block.get("source"),
                    "condition_ref": str(block.get("condition_ref")),
                    "activity_ref": block.get("activity_ref"),
                }
                answer = {"decision": "PAUSE",
                          "reason_codes": [PAUSE_REASON],
                          "reevaluation_hint_ref": str(block["condition_ref"]),
                          "later_item": {"title": "歇一会儿再回到那件练习",
                                         "park_current": True,
                                         "in_minutes": PARK_MINUTES}}
        self.answers.append({"decision": answer.get("decision")})
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


def _bare(ref: Any) -> str:
    text = str(ref or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _active_artifact(runtime: Any) -> tuple[Optional[str], Optional[str]]:
    """Find the ACTIVE Activity this round started and its bound Artifact ref."""

    for activity in runtime.activity_read.list_open_activities():
        if str(activity.get("status") or "") != "ACTIVE":
            continue
        refs: list[str] = []
        for field in ("subject_refs", "target_ref", "checkpoint_ref"):
            raw = activity.get(field)
            values = raw if isinstance(raw, (list, tuple)) else [raw]
            for value in values:
                text = str(value or "")
                if text.startswith("artifact:") and text not in refs:
                    refs.append(text)
        if refs:
            return str(activity.get("activity_id") or ""), refs[0]
    return None, None


def _pause_bound_conditions(runtime: Any) -> list[dict[str, Any]]:
    store = getattr(runtime, "condition_store", None)
    loader = getattr(store, "load_state", None)
    if not callable(loader):
        return []
    try:
        state = loader() or {}
    except Exception:
        return []
    rows = state.get("conditions") if isinstance(state, Mapping) else None
    if not isinstance(rows, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for cid, row in rows.items():
        if not isinstance(row, Mapping):
            continue
        params = row.get("parameters") if isinstance(row.get("parameters"), Mapping) else {}
        if params.get("pause_bound") is True:
            out.append({"condition_id": str(cid),
                        "status": str(row.get("status") or ""),
                        "condition_kind": str(row.get("condition_kind") or ""),
                        "activity_id": str(row.get("activity_id") or "")})
    return out


def _open_eligibilities(runtime: Any) -> list[dict[str, str]]:
    store = getattr(runtime, "eligibility_store", None)
    loader = getattr(store, "load_state", None)
    if not callable(loader):
        return []
    try:
        state = loader() or {}
    except Exception:
        return []
    rows = state.get("eligibilities") if isinstance(state, Mapping) else None
    if not isinstance(rows, Mapping):
        return []
    return [{"eligibility_id": str(eid),
             "status": str((row or {}).get("status") or ""),
             "activity_id": str((row or {}).get("activity_id") or "")}
            for eid, row in rows.items() if isinstance(row, Mapping)]


def _gate_view(gate: Mapping[str, Any]) -> dict[str, Any]:
    """从结构门的最终候选里核对 H 的 RESUME、另一件的 START 与 NO_ACTION。"""

    targets = list(gate.get("candidate_targets") or [])
    h_bare = _bare(gate.get("h_ref") or "")
    resume_h = [row for row in targets
                if any(_bare(ref) == h_bare for ref in (row.get("targets") or ()))
                and "RESUME" in (row.get("legal") or ())]
    start_other = [row for row in targets
                   if not any(_bare(ref) == h_bare for ref in (row.get("targets") or ()))
                   and "START" in (row.get("legal") or ())
                   and any(_bare(ref) in set(gate.get("other_works") or ())
                           for ref in (row.get("targets") or ()))]
    return {"h_has_resume": bool(resume_h),
            "other_work_has_start": bool(start_other),
            "no_action_legal": bool(gate.get("no_action_legal")),
            "candidate_count": gate.get("candidate_count"),
            "executable_count": gate.get("executable_count"),
            "legal_decision_enums": list(gate.get("legal_decision_enums") or ())}


def run() -> dict[str, Any]:
    restore_http = v2._hard_disconnect(http.client.HTTPConnection, "connect")
    scratch_parent = Path(tempfile.mkdtemp(prefix="g2-dual-choice-"))
    plan = v2.DEFAULT_PLAN
    decide = DualChoiceTransport()
    content = v2.ScriptedAppendTransport()
    context = v2._open_scratch(scratch_parent, "instance-dual", plan,
                               decision_transport=decide, content_transport=content)
    steps: list[dict[str, Any]] = []
    if not context.get("ok"):
        restore_http()
        return {"ok": False, "reason_code": _code(context.get("reason_code"), "SETUP_FAILED"),
                "scratch_parent": str(scratch_parent), "steps": steps}
    bundle = context["bundle"]
    cycle = bundle["cycle"]
    runtime = bundle["runtime"]
    # 所有返回值先写进同一个 sink，finally 里补 cleanup 状态（不返回也不吞错误）。
    sink: dict[str, Any] = {}

    def _emit(payload: Mapping[str, Any]) -> dict[str, Any]:
        sink.clear()
        sink.update(payload)
        return sink

    try:
        # 1) 真实一轮：脚本 START → 真 writer → 真 owner 版本（H 成立）
        tick1 = v2._advance_from_runner(bundle)()
        h_activity, h_ref = _active_artifact(runtime)
        owner_evidence = tick1.get("extra") if isinstance(tick1.get("extra"), Mapping) else {}
        steps.append({"step": "start", "status": tick1.get("status"),
                      "decision": tick1.get("decision"),
                      "llm_calls": tick1.get("llm_calls"),
                      "owner_version_created": owner_evidence.get("owner_version_created"),
                      "h_activity": h_activity, "h_ref": h_ref})
        if not h_ref:
            return _emit(_finish(restore_http, scratch_parent, steps,
                                 "H_NOT_CREATED",
                                 "no ACTIVE Activity bound to an Artifact after START"))

        # 2) 真实一轮：脚本 PAUSE（抄本轮 offer 的 park 条件；条件由 owner 绑定）
        tick2 = v2._advance_from_runner(bundle)()
        paused = runtime.activity_read.get_activity(str(h_activity)) or {}
        view = runtime.activity_read.get_current_life_view() or {}
        bound = _pause_bound_conditions(runtime)
        steps.append({"step": "pause", "status": tick2.get("status"),
                      "decision": tick2.get("decision"),
                      "llm_calls": tick2.get("llm_calls"),
                      "status_after": str(paused.get("status") or ""),
                      "foreground_after": view.get("foreground_activity_ref"),
                      "offered_pause": decide.offered_pause,
                      "bound_conditions": bound})
        if str(paused.get("status") or "") != "PAUSED":
            return _emit(_finish(restore_http, scratch_parent, steps, "H_NOT_PAUSED",
                                 "the owner did not report H as PAUSED after the PAUSE round"))
        if not bound:
            return _emit(_finish(restore_http, scratch_parent, steps, "NO_PAUSE_CONDITION_BOUND",
                                 "the committed PAUSE named no condition, so no resume eligibility "
                                 "can ever exist"))

        # 3) 正式维护（夹具时钟 +时限）：LR-4 自己的 due 扫描判 TIME_AT_OR_AFTER 满足
        upkeep = cycle.maintain_waiting(observed_at=_after(ADVANCE_MARGIN_MINUTES))
        eligibilities = _open_eligibilities(runtime)
        open_for_h = [row for row in eligibilities
                      if row["status"] == "OPEN" and row["activity_id"] == str(h_activity)]
        steps.append({"step": "maintain", "state": upkeep.get("state"),
                      "checked": upkeep.get("checked"),
                      "satisfied": list(upkeep.get("satisfied") or ()),
                      "emitted": list(upkeep.get("emitted_eligibilities") or ()),
                      "due_agenda": upkeep.get("due_agenda"),
                      "errors": [_code(item, "ERROR")
                                 for item in (upkeep.get("errors") or ())],
                      "conditions_after": _pause_bound_conditions(runtime),
                      "open_for_h": open_for_h})
        if not open_for_h:
            return _emit(_finish(restore_http, scratch_parent, steps, "ELIGIBILITY_NOT_OPEN",
                                 "LR-4 did not turn the pause-bound time condition into an OPEN "
                                 "eligibility"))

        # 4) 现成正式结构门：同一冻结结构里三件事是否都合法
        gate = v2.freeze_and_check(bundle, h_ref=h_ref,
                                   work_refs=["artifact:%s" % row["artifact_id"]
                                              for row in context["prepared"]["works"]],
                                   sleep=lambda _seconds: None)
        gate_view: dict[str, Any] = {}
        if gate.get("ok"):
            raw = dict(gate.get("gate") or {})
            raw["h_ref"] = h_ref
            raw["other_works"] = [_bare("artifact:%s" % row["artifact_id"])
                                  for row in context["prepared"]["works"]]
            gate_view = _gate_view(raw)
        steps.append({"step": "gate", "ok": bool(gate.get("ok")),
                      "reason_code": _code(gate.get("reason_code"),
                                           "OK" if gate.get("ok") else "GATE_FAILED"),
                      "view": gate_view})
        checks = {
            "start_round_ran": bool(h_activity) and bool(h_ref),
            "pause_round_ran": str(paused.get("status") or "") == "PAUSED",
            "pause_condition_offered": bool(decide.offered_pause.get("condition_ref")),
            "pause_condition_bound": bool(bound),
            "eligibility_open_for_h": bool(open_for_h),
            "gate_ok": bool(gate.get("ok")),
            "h_has_resume": bool(gate_view.get("h_has_resume")),
            "other_work_has_start": bool(gate_view.get("other_work_has_start")),
            "no_action_legal": bool(gate_view.get("no_action_legal")),
        }
        blocking = [key for key, value in checks.items() if not value]
        return _emit({"ok": not blocking, "blocking": blocking, "checks": checks,
                      "scratch_parent": str(scratch_parent),
                      "scratch_root": str(context.get("root")),
                      "requests": len(decide.calls) + len(content.calls),
                      "budget": _budget_view(context.get("budget")),
                      "steps": steps})
    except Exception as exc:  # 具名失败，不吞
        steps.append({"step": "exception", "kind": type(exc).__name__})
        return _emit({"ok": False, "reason_code": "PROBE_EXCEPTION",
                      "blocking": ["exception"],
                      "scratch_parent": str(scratch_parent), "steps": steps})
    finally:
        # 关闭调用照旧执行；这里只记录调用结果，不宣称已 drain / 已证实停止。
        release_state = "skipped"
        if bundle is not None:
            try:
                baseline.release_runtime(bundle)
                release_state = "ok"
            except Exception as exc:
                release_state = "error:%s" % type(exc).__name__
        supply_state = "skipped"
        supply = context.get("supply")
        if supply is not None:
            try:
                supply.stop()
                supply_state = "ok"
            except Exception as exc:
                supply_state = "error:%s" % type(exc).__name__
        restore_http()
        if sink:
            sink["cleanup"] = {"release_runtime": release_state,
                               "supply_stop": supply_state,
                               "result_verified": False}


def _finish(restore_http: Any, scratch_parent: Path, steps: list[dict[str, Any]],
            reason: str, detail: str) -> dict[str, Any]:
    restore_http()
    return {"ok": False, "reason_code": reason, "detail": detail,
            "blocking": [reason], "scratch_parent": str(scratch_parent), "steps": steps}


def main(argv: Optional[list[str]] = None) -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return EXIT_OK if result.get("ok") else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
