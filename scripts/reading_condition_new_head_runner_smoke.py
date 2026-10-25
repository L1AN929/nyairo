#!/usr/bin/env python3
"""零模型真实链：阅读材料换新 head -> LR-4 条件满足 -> OPEN 资格 -> 回到候选。

这条链只回答一件事（本组唯一目标）
----------------------------------
1. 真实 Supply owner + 正式 Life runtime、脚本 Decision/内容替身，在全新的隔离根里准备一个
   带**真实阅读材料**（分页内容）和**真实私人事项文档**的实例；另建一件不同的作品 B。
2. 正式 START 阅读 A，逐页 CONTINUE 到这一版读完（``READING_EXHAUSTED``，真实停点）。
3. 脚本 Decision 显式使用本轮 offered 的暂停条件（``READING_MATERIAL_NEXT_VERSION``）选择
   PAUSE：真 LR-2 暂停、前台释放、真 LR-4 绑定条件且条件 PENDING。
4. 脚本 Decision 正式 START B（不同 Artifact）。B 执行、A 仍 PAUSED，条件不变。
5. 通过真 Supply owner 正式提交 A 的新 head；B 仍 ACTIVE、前台仍是 B。
6. 正式 ``AutonomousLifeRunner.run_due`` 走既有维护：本窗 **NOT_DUE**（注入时钟/既有窗口），
   维护返回里条件 SATISFIED、只有一条 OPEN 资格，且认知替身计数**不增**。
7. B 执行期间走正式 runner/cycle 候选面：A 的 OPEN 资格**不可执行 RESUME**（offered 里没有
   A 的 RESUME 候选），A 仍 PAUSED、资格不消费、B 不被打断。
8. 脚本 Decision 合法 PAUSE B、释放前台；下一合法 due 轮经正式候选路径读到 A 的
   ``RESUME_ELIGIBLE`` 候选；脚本 Decision 选 NO_ACTION，保持 A PAUSED / 资格 OPEN。
   **本组答案是“回到候选”，不是自然恢复。**

边界（明示，不冒认）
--------------------
* Decision 与内容/阅读响应都是脚本替身；没有产品模型调用、没有网络、没有真实日常。
* 阅读材料、私人事项文档、作品 B 和 A 的新 head 都是真实 Supply owner 状态；种子文本是
  operator fixture，不是她的创作。
* 本链证明的是机制接线（条件 SATISFIED、资格 OPEN、前台冲突下不可执行、释放后回候选），
  不是“真模型会自然这样选”，也不是 G1、不是 24h、不是 l 历史或任何旧实例证据。
* 时间用注入的 Life 时刻推进（只为让既有 check 窗口到期），不睡墙钟、不改生产窗口配置。

运行：
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/reading_condition_new_head_runner_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_continuity as ac  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import life_wait_basis as lwb  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402

from tests.test_one_day_life import Services  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_READING_CONDITION_SMOKE_ROOT")
               or "/tmp/nyairo-reading-condition-new-head")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"

#: 真实阅读材料的 operator 文本：三页，读完第三页后这一版就是 EXHAUSTED。
READING_PAGE_MARKER = "%%PAGE%%"
READING_PAGES = ("第一页的正文内容。", "第二页的正文内容。", "第三页的正文内容。")
READING_CONTENT = READING_PAGE_MARKER.join(READING_PAGES)
READING_SOURCE = lwb.SOURCE_READING

CLOSEOUT_REF_PREFIX = "closeout:"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("SMOKE_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:6000])
    raise SystemExit(1)


def _bare_identity(ref) -> str:
    text = str(ref or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _candidate_refs(row) -> list[str]:
    return [str(ref) for ref in (row.get("target_refs") or [])]


def _is_closeout_candidate(row) -> bool:
    return any(ref.startswith(CLOSEOUT_REF_PREFIX) for ref in _candidate_refs(row))


def _matches_artifact(row, artifact_id) -> bool:
    wanted = _bare_identity(artifact_id)
    if not wanted:
        return False
    refs = _candidate_refs(row) + [str(row.get("source_ref") or "")]
    return any(_bare_identity(ref) == wanted for ref in refs)


def _start_matches(candidates, artifact_id) -> list[dict]:
    return [row for row in candidates
            if not _is_closeout_candidate(row)
            and _matches_artifact(row, artifact_id)
            and "START" in (row.get("legal_decisions") or [])]


class _ScriptedDecision:
    """脚本 Decision：只在 AG-1 真正给的合法集合里选择，返回原始 JSON。"""

    model_id = "stub:reading-condition-new-head"
    model_version = "reading-condition-new-head-v1"

    def __init__(self, *, reading_id: str, other_id: str) -> None:
        self.reading_id = str(reading_id)
        self.other_id = str(other_id)
        self.policy = "start_reading"
        self.calls = 0
        self.last_offered: dict = {}
        self.last_selection: dict = {}
        self.offered_history: list[dict] = []

    def _raw(self, candidates, allowed):
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.policy == "start_reading":
            matches = _start_matches(candidates, self.reading_id)
            self.last_selection = {"policy": self.policy,
                                   "matches": [row.get("candidate_id") for row in matches]}
            if matches:
                return {"decision": "START", "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start reading A",
                        "deferred_candidate_ids": []}
        if self.policy == "continue_reading":
            rows = [row for row in candidates
                    if "CONTINUE" in (row.get("legal_decisions") or [])]
            self.last_selection = {"policy": self.policy,
                                   "continue_candidates": [row.get("candidate_id") for row in rows]}
            if rows:
                return {"decision": "CONTINUE", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: read the next page",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_with_condition":
            offered = self.last_offered.get("pause_condition") or {}
            pause_candidates = [row for row in candidates
                                if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {
                "policy": self.policy,
                "offered_source": offered.get("source"),
                "offered_available": offered.get("available"),
                "pause_candidates": [row.get("candidate_id") for row in pause_candidates],
            }
            if (offered.get("available") and offered.get("condition_ref")
                    and str(offered.get("source") or "") == READING_SOURCE
                    and pause_candidates):
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reevaluation_hint_ref": str(offered["condition_ref"]),
                        "reason_codes": reason,
                        "short_rationale": "stub: pause A until the material gets a new version",
                        "deferred_candidate_ids": []}
        if self.policy == "start_other":
            matches = _start_matches(candidates, self.other_id)
            self.last_selection = {"policy": self.policy,
                                   "matches": [row.get("candidate_id") for row in matches]}
            if matches:
                return {"decision": "START", "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start work B",
                        "deferred_candidate_ids": []}
        if self.policy == "advance_other":
            # B 执行的一轮：先证明 A 的 RESUME 不在可执行集合里，再让 B 自己真的走一步，
            # 这样这一轮不是空转，下一次同样状态也不会被 reconsideration 当成重复。
            rows = [row for row in candidates
                    if "CONTINUE" in (row.get("legal_decisions") or [])
                    and _matches_artifact(row, self.other_id)]
            self.last_selection = {"policy": self.policy,
                                   "continue_candidates": [row.get("candidate_id")
                                                           for row in rows]}
            if rows:
                return {"decision": "CONTINUE", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: carry on with B",
                        "deferred_candidate_ids": []}
        if self.policy == "resume_a":
            rows = [row for row in candidates
                    if "RESUME" in (row.get("legal_decisions") or [])
                    and (str(row.get("resume_eligibility_ref") or "")
                         or _matches_artifact(row, self.reading_id))]
            self.last_selection = {"policy": self.policy,
                                   "resume_candidates": [row.get("candidate_id") for row in rows]}
            if len(rows) == 1:
                return {"decision": "RESUME", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: resume A if offered",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_other":
            rows = [row for row in candidates
                    if "PAUSE" in (row.get("legal_decisions") or [])
                    and _matches_artifact(row, self.other_id)]
            pause_candidates = rows or [row for row in candidates
                                        if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {"policy": self.policy,
                                   "pause_candidates": [row.get("candidate_id")
                                                        for row in pause_candidates]}
            if pause_candidates:
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: pause B",
                        "deferred_candidate_ids": []}
        self.last_selection = {"policy": self.policy}
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": ["NO_ACTION_CHOSEN"],
                "short_rationale": "stub: no action", "deferred_candidate_ids": []}

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        situation = dict(getattr(request, "situation_context", None) or {})
        self.last_offered = {
            "policy": self.policy,
            "pause_condition": situation.get("pause_condition"),
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "resume_eligibility_ref": row.get("resume_eligibility_ref"),
                            "target_refs": row.get("target_refs"),
                            "legal_decisions": row.get("legal_decisions")} for row in candidates],
        }
        self.offered_history.append(self.last_offered)
        raw = self._raw(candidates, allowed)
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:reading-condition-new-head:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _ContentWriter:
    """内容替身：作品追加普通文本；阅读响应固定 ``no_change``（不写批注）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.response_calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        return "%s\n\n（脚本替身追加）%s\n" % (getattr(point, "content", "") or "", observed_at)

    def respond(self, context, *, observed_at: str) -> dict:
        self.response_calls += 1
        return {"kind": "no_change"}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _conditions(runtime) -> dict:
    state = runtime.waiting_coordinator.condition_store.load_state() or {}
    return dict(state.get("conditions") or {})


def _eligibilities(runtime) -> dict:
    state = runtime.waiting_coordinator.eligibility_store.load_state() or {}
    return dict(state.get("eligibilities") or {})


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _canonical(runtime) -> dict:
    state, _journal = runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def _active_ids(state: dict) -> list[str]:
    return [aid for aid, row in (state.get("activities") or {}).items()
            if str(row.get("status") or "") == ac.STATUS_ACTIVE]


def _assert_single_execution(runtime, expected_fg, *, where: str) -> dict:
    state = _canonical(runtime)
    active = _active_ids(state)
    _check(len(active) <= 1, "%s: more than one ACTIVE Activity" % where,
           {"active": active, "foreground": state.get("foreground_activity_ref")})
    _check(state.get("foreground_activity_ref") == expected_fg,
           "%s: foreground is not the expected Activity" % where,
           {"expected": expected_fg, "state_foreground": state.get("foreground_activity_ref"),
            "active": active})
    if expected_fg is None:
        _check(active == [], "%s: an ACTIVE Activity exists without a foreground" % where, active)
    return state


def _open_eligibilities(runtime) -> list[dict]:
    return [row for row in _eligibilities(runtime).values()
            if str(row.get("status") or "") == "OPEN"]


def _offer_has_resume_for(offered, *, eligibility_id: str, artifact_id: str) -> bool:
    """候选面里有没有一条可执行（legal 含 RESUME）且指向 A 的候选。"""

    for row in (offered.get("candidates") or []):
        if "RESUME" not in (row.get("legal_decisions") or []):
            continue
        if str(row.get("resume_eligibility_ref") or "") == str(eligibility_id):
            return True
        if _matches_artifact(row, artifact_id):
            return True
    return False


def run_smoke(root=None) -> dict:
    """跑完整一条链，返回 summary（只含 ID / 枚举 / 计数 / 原因码）。"""

    run_root = Path(root) if root is not None else RUN
    run_root.mkdir(parents=True, exist_ok=True)
    services = Services(run_root / "services")
    runtime = None
    summary: dict = {"root": str(run_root), "checks": {}, "limitations": [
        "Decision / content / reading response are scripted doubles; no product model was called",
        "the reading material, private-items document, work B and A's new head are real owner state; "
        "the seed texts are operator fixtures",
        "the injected Life clock only opens the existing check window; no wall-clock wait",
        "mechanism evidence only: not a natural model choice, not G1, not a 24h run",
    ]}
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:reading-condition-new-head",
            title="阅读条件新 head 冒烟",
            reading_title="真实阅读材料（operator 提供）",
            reading_content=READING_CONTENT,
            create_work_artifact=False,
            create_private_items=True)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        reading_id = str(data.get("reading_artifact_id") or "")
        private_id = str(data.get("private_items_artifact_id") or "")
        _check(bool(reading_id), "the instance has no reading material", data)
        _check(bool(private_id), "the instance has no private-items document", data)

        env = odl.instance_environment(instance)
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        state_root = odl.life_state_root(instance)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        created_b = port.create_artifact(
            workspace_id=str(data["workspace_id"]),
            title="另一件作品（脚本夹具）",
            content="作品 B 的第一句。\n",
            grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("reading-condition-new-head", "create-b", "b"))
        _check(created_b.applied and bool(created_b.artifact_id),
               "the owner did not create work B", created_b.to_dict())
        other_id = str(created_b.artifact_id)

        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecision(reading_id=reading_id, other_id=other_id)
        writer = _ContentWriter()
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer,
            reading_response_writer=writer, enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        runner = assembled["runner"]
        base = datetime.now(timezone.utc)

        def _turn(policy: str, trigger: str, moment: datetime) -> dict:
            cognition.policy = policy
            return cycle.evaluate_once(trigger_ref=trigger, observed_at=_iso(moment), act=True)

        # 1. 正式 START 阅读 A，然后逐页 CONTINUE 到这一版真的读完
        first = _turn("start_reading", "evt:reading-cond:1", base)
        state = _canonical(runtime)
        active = _active_ids(state)
        _check(len(active) == 1, "round 1 did not leave exactly one ACTIVE Activity", state)
        activity_a = str(active[0])
        _assert_single_execution(runtime, activity_a, where="round1-start-reading")
        _check(first.get("adoption_status") == "APPLIED",
               "the reading START was not adopted", first)
        head_a1 = str((port.read_head(reading_id) or {}).get("version_id") or "")
        _check(bool(head_a1), "the reading material has no committed head", {})

        read_pages = 0
        for page in (2, 3):
            out = _turn("continue_reading", "evt:reading-cond:read-%d" % page, base)
            situation = cycle.private_material_situation(refresh=True) or {}
            _check(out.get("outcome") in {"ACTED", "ALREADY_DONE"},
                   "reading page %d was not acted" % page,
                   {"outcome": out.get("outcome"), "decision": out.get("decision"),
                    "detail": out.get("detail"), "state": situation.get("state")})
            read_pages += 1
        situation = cycle.private_material_situation(refresh=True) or {}
        _check(str(situation.get("state") or "") == "EXHAUSTED",
               "the material is not at its real stop point after the last page",
               {"state": situation.get("state"),
                "pages": situation.get("pages"),
                "position": situation.get("position")})
        summary["read"] = {"pages_read": read_pages + 1,
                           "state": situation.get("state"),
                           "material_pages": situation.get("pages")}

        # 2. 显式 offered head 依赖：PAUSE A -> 真 LR-2 暂停释放 + 真 LR-4 条件 PENDING
        fourth = _turn("pause_with_condition", "evt:reading-cond:pause-a", base)
        offered = dict(cognition.last_offered.get("pause_condition") or {})
        _check(bool(offered.get("available")), "no pause condition was offered", offered)
        _check(str(offered.get("source") or "") == READING_SOURCE,
               "the offered condition is not the reading-material source", offered)
        _check(fourth.get("decision") == "PAUSE" and fourth.get("adoption_status") == "APPLIED",
               "the conditional PAUSE was not adopted", fourth)
        paused_a = _activity(runtime, activity_a)
        _check(paused_a.get("status") == ac.STATUS_PAUSED, "A is not PAUSED", paused_a)
        _check(paused_a.get("foreground_released") is True,
               "A's PAUSE did not release the foreground", paused_a)
        _assert_single_execution(runtime, None, where="round2-pause-a")
        conditions = _conditions(runtime)
        _check(len(conditions) == 1, "no single pause condition was bound", conditions)
        condition = next(iter(conditions.values()))
        condition_id = str(condition.get("condition_id") or "")
        _check(str(condition.get("status") or "") == "PENDING",
               "the bound condition is not PENDING", condition)
        _check(str(condition.get("activity_id")) == activity_a,
               "the condition is not bound to A", condition)
        _check(_open_eligibilities(runtime) == [],
               "a PAUSE minted a resume eligibility", _eligibilities(runtime))
        summary["pause_a"] = {"decision": fourth.get("decision"),
                              "activity_a_status": paused_a.get("status"),
                              "condition_id": condition_id,
                              "condition_status": condition.get("status"),
                              "offered_source": offered.get("source")}

        # 3. 脚本 Decision 正式 START B（不同 Artifact），A 保持 PAUSED
        fifth = _turn("start_other", "evt:reading-cond:start-b", base)
        _check(fifth.get("decision") == "START" and fifth.get("adoption_status") == "APPLIED",
               "round 3 did not START work B",
               {key: fifth.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error")}
               | {"selection": cognition.last_selection})
        state = _canonical(runtime)
        active = _active_ids(state)
        _check(len(active) == 1, "round 3 did not leave exactly one ACTIVE Activity", state)
        activity_b = str(active[0])
        _check(activity_b != activity_a, "round 3 reused A as the running Activity", state)
        _assert_single_execution(runtime, activity_b, where="round3-start-b")
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "starting B changed A's status", _activity(runtime, activity_a))
        _check(str(next(iter(_conditions(runtime).values())).get("status") or "") == "PENDING",
               "starting B changed the condition", _conditions(runtime))
        summary["start_b"] = {"decision": fifth.get("decision"),
                              "activity_b": activity_b,
                              "activity_a_status": ac.STATUS_PAUSED,
                              "foreground": activity_b}

        # 4. 先让 runner 走一轮 due 窗口（既有入口），把 last_check_at 落在注入时刻上；
        #    这一轮维护还没有新 head，只能报 unchanged。
        warm = runner.run_due(act=True, now=base)
        warm_waiting = dict((warm.extra or {}).get("waiting") or {}) if warm.extra else {}
        _check(warm.status != "ERROR", "the warm-up due window raised", warm.to_dict())
        summary["window_warm"] = {"status": warm.status,
                                  "waiting_state": warm_waiting.get("state"),
                                  "waiting_checked": warm_waiting.get("checked"),
                                  "waiting_changed": warm_waiting.get("changed")}

        # 5. 真 Supply owner 正式提交 A 的新 head；B 仍 ACTIVE、前台仍是 B
        head_a2_outcome = port.commit_version(
            artifact_id=reading_id, expected_head=head_a1,
            content="%s\n\n%s第四页（owner 真实提交的新版本）" % (READING_CONTENT,
                                                          READING_PAGE_MARKER),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("reading-condition-new-head", "v2-a", "a"))
        _check(head_a2_outcome.applied, "the owner did not commit the new reading head",
               head_a2_outcome.to_dict())
        head_a2 = str((port.read_head(reading_id) or {}).get("version_id") or "")
        _check(head_a2 and head_a2 != head_a1, "the owner's head did not change",
               {"before": head_a1, "after": head_a2})
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "committing A's head changed B", _activity(runtime, activity_b))
        _assert_single_execution(runtime, activity_b, where="after-new-head")

        # 6. 正式 runner 的**下一个窗口 NOT_DUE**：维护仍然跑，条件由 LR-4 自己满足并发出
        #    一条 OPEN 资格；这一轮不评估、不花认知。
        calls_before_not_due = cognition.calls
        not_due = runner.run_due(act=True, now=base + timedelta(seconds=60))
        calls_after_not_due = cognition.calls
        waiting = dict((not_due.extra or {}).get("waiting") or {}) if not_due.extra else {}
        _check(not_due.status == "NOT_DUE", "the second check window was not NOT_DUE",
               {"status": not_due.status, "reason": not_due.reason,
                "detail": not_due.detail, "waiting": waiting})
        _check(calls_after_not_due == calls_before_not_due,
               "the NOT_DUE window spent a cognition request",
               {"before": calls_before_not_due, "after": calls_after_not_due})
        _check(str(waiting.get("state") or "") == "OK",
               "the maintenance report is not OK", waiting)
        _check(list(waiting.get("satisfied") or []) == [condition_id],
               "LR-4 did not satisfy exactly the bound condition",
               {"waiting": waiting, "condition_id": condition_id})
        _check(len(list(waiting.get("emitted_eligibilities") or [])) == 1,
               "maintenance did not emit exactly one eligibility", waiting)
        _check(int(waiting.get("llm_calls") or 0) == 0 and not waiting.get("auto_resumed"),
               "maintenance spent a request or resumed by itself", waiting)
        satisfied_condition = _conditions(runtime).get(condition_id) or {}
        _check(str(satisfied_condition.get("status") or "") == "SATISFIED",
               "the condition is not SATISFIED", satisfied_condition)
        open_rows = _open_eligibilities(runtime)
        _check(len(open_rows) == 1, "there is not exactly one OPEN eligibility", open_rows)
        eligibility_id = str(open_rows[0].get("eligibility_id") or "")
        _check(str(open_rows[0].get("activity_id")) == activity_a,
               "the OPEN eligibility is not for A", open_rows[0])
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "maintenance resumed A by itself", _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "maintenance changed the running B", _activity(runtime, activity_b))
        _assert_single_execution(runtime, activity_b, where="after-maintain")
        b_active_after_maintenance = (
            _activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE)
        summary["not_due_maintenance"] = {
            "status": not_due.status,
            "waiting_state": waiting.get("state"),
            "checked": waiting.get("checked"),
            "changed": waiting.get("changed"),
            "satisfied": list(waiting.get("satisfied") or []),
            "emitted_eligibilities": list(waiting.get("emitted_eligibilities") or []),
            "llm_calls": waiting.get("llm_calls"),
            "auto_resumed": waiting.get("auto_resumed"),
            "cognition_delta": calls_after_not_due - calls_before_not_due,
            "condition_status": satisfied_condition.get("status"),
            "eligibility_id": eligibility_id,
            "eligibility_status": open_rows[0].get("status"),
        }

        # 7. B 执行期间：正式候选面不得给出可执行的 A RESUME；A/OPEN 不消费、B 不被打断
        blocked = _turn("advance_other", "evt:reading-cond:b-active",
                        base + timedelta(minutes=31))
        blocked_offer = dict(cognition.last_offered or {})
        _check(not _offer_has_resume_for(blocked_offer, eligibility_id=eligibility_id,
                                         artifact_id=reading_id),
               "A's RESUME was executable while B held the foreground",
               {"offered": blocked_offer, "eligibility_id": eligibility_id})
        _check(blocked.get("decision") != "RESUME",
               "a RESUME of A was chosen while B ran", blocked)
        _check(blocked.get("decision") == "CONTINUE",
               "B did not advance while A was held back", blocked)
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "A changed while B ran", _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "B stopped running", _activity(runtime, activity_b))
        _check(str((_eligibilities(runtime).get(eligibility_id) or {}).get("status") or "")
               == "OPEN", "A's eligibility was consumed while B ran", _eligibilities(runtime))
        _assert_single_execution(runtime, activity_b, where="b-active-resume-refused")
        summary["background_conflict"] = {
            "decision": blocked.get("decision"),
            "resume_offered": False,
            "activity_a_status": ac.STATUS_PAUSED,
            "activity_b_status": ac.STATUS_ACTIVE,
            "eligibility_status": "OPEN",
        }

        # 8. 脚本 Decision 合法 PAUSE B、释放前台
        pause_b = _turn("pause_other", "evt:reading-cond:pause-b", base + timedelta(minutes=31))
        _check(pause_b.get("decision") == "PAUSE" and pause_b.get("adoption_status") == "APPLIED",
               "round 6 did not pause B",
               {key: pause_b.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error")}
               | {"selection": cognition.last_selection})
        paused_b = _activity(runtime, activity_b)
        _check(paused_b.get("status") == ac.STATUS_PAUSED, "B is not PAUSED", paused_b)
        _check(paused_b.get("foreground_released") is True,
               "B's PAUSE did not release the foreground", paused_b)
        _assert_single_execution(runtime, None, where="round6-pause-b")
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "pausing B resumed A by itself", _activity(runtime, activity_a))
        _check(str((_eligibilities(runtime).get(eligibility_id) or {}).get("status") or "")
               == "OPEN", "pausing B consumed A's eligibility", _eligibilities(runtime))
        summary["pause_b"] = {"decision": pause_b.get("decision"),
                              "activity_b_status": paused_b.get("status"),
                              "foreground": None,
                              "eligibility_status": "OPEN"}

        # 9. 前台空后的下一个合法 due 轮：正式候选面读到 A 的 RESUME_ELIGIBLE；
        #    脚本 Decision 选 NO_ACTION，保持 A/OPEN（本组答案是“回到候选”）。
        cognition.policy = "no_action"
        back = runner.run_due(act=True, now=base + timedelta(minutes=63))
        back_offer = dict(cognition.last_offered or {})
        _check(back.status != "ERROR", "the return check window raised", back.to_dict())
        _check(_offer_has_resume_for(back_offer, eligibility_id=eligibility_id,
                                     artifact_id=reading_id),
               "A did not return as an executable RESUME candidate with the slot free",
               {"offered": back_offer, "eligibility_id": eligibility_id,
                "outcome": back.outcome, "decision": back.decision,
                "waiting": (back.extra or {}).get("waiting")})
        resume_rows = [row for row in (back_offer.get("candidates") or [])
                       if "RESUME" in (row.get("legal_decisions") or [])]
        _check(len(resume_rows) == 1, "the return offer did not name exactly one RESUME candidate",
               {"candidates": back_offer.get("candidates"), "eligibility_id": eligibility_id})
        _check(str(resume_rows[0].get("candidate_kind") or "") == "RESUME_ACTIVITY"
               and str(resume_rows[0].get("source_kind") or "") == "RESUME_ELIGIBLE"
               and _matches_artifact(resume_rows[0], reading_id),
               "the RESUME candidate is not A's eligible one", resume_rows[0])
        _check(back.decision == "NO_ACTION", "the scripted NO_ACTION was not committed",
               {"decision": back.decision, "outcome": back.outcome,
                "detail": back.detail})
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "A was resumed although the Decision chose NO_ACTION",
               _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_PAUSED,
               "B changed on the return window", _activity(runtime, activity_b))
        _check(str((_eligibilities(runtime).get(eligibility_id) or {}).get("status") or "")
               == "OPEN", "A's eligibility was consumed by NO_ACTION", _eligibilities(runtime))
        _assert_single_execution(runtime, None, where="return-no-action")
        summary["return_candidate"] = {
            "status": back.status,
            "decision": back.decision,
            "candidate_kind": resume_rows[0].get("candidate_kind"),
            "source_kind": resume_rows[0].get("source_kind"),
            "target_artifact_matches_a": _matches_artifact(resume_rows[0], reading_id),
            "activity_a_status": ac.STATUS_PAUSED,
            "activity_b_status": ac.STATUS_PAUSED,
            "eligibility_status": "OPEN",
            "foreground": None,
        }

        summary["checks"] = {
            "reading_started": activity_a != activity_b,
            "reading_reached_real_stop": str(situation.get("state")) == "EXHAUSTED",
            "pause_bound_pending_condition": (len(conditions) == 1
                                              and str(condition.get("status")) == "PENDING"),
            "pause_released_foreground": paused_a.get("foreground_released") is True,
            "b_started_as_other_activity": activity_b != activity_a,
            "new_head_committed_by_owner": head_a2 != head_a1,
            "b_stayed_active_after_maintenance": b_active_after_maintenance,
            "not_due_window": not_due.status == "NOT_DUE",
            "maintenance_zero_cognition": calls_after_not_due == calls_before_not_due,
            "condition_satisfied": str(satisfied_condition.get("status")) == "SATISFIED",
            "one_open_eligibility_for_a": (len(open_rows) == 1
                                           and str(open_rows[0].get("activity_id")) == activity_a),
            "maintenance_no_auto_resume": not waiting.get("auto_resumed"),
            "background_resume_not_executable": True,
            "background_left_a_paused_open": True,
            "pause_b_released_foreground": paused_b.get("foreground_released") is True,
            "a_returned_as_candidate": bool(resume_rows),
            "no_action_kept_a_open": True,
            "single_execution_throughout": True,
        }
        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        summary["model_calls"] = cognition.calls
        summary["writer_calls"] = writer.calls
        summary["reading_response_calls"] = writer.response_calls
        summary["ids"] = {"reading": reading_id, "other": other_id,
                          "activity_a": activity_a, "activity_b": activity_b,
                          "condition": condition_id, "eligibility": eligibility_id}
        return summary
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        try:
            services.stop()
        except Exception:
            pass


def main() -> int:
    summary = run_smoke()
    passed = bool(summary.get("ok"))
    print("SMOKE_OK" if passed else "SMOKE_FAIL")
    print(json.dumps({key: summary.get(key) for key in (
        "root", "ids", "read", "pause_a", "start_b", "window_warm",
        "not_due_maintenance", "background_conflict", "pause_b", "return_candidate",
        "checks", "model_calls", "writer_calls", "reading_response_calls", "limitations", "ok")},
        ensure_ascii=False, default=str, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
