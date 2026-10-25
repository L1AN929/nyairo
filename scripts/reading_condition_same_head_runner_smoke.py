#!/usr/bin/env python3
"""零模型真实链：同一 head 的后台 PAUSE 阅读，NOT_DUE 仍复查、但不发资格。

这条链只回答一件事（本组唯一目标）
----------------------------------
1. 真实 Supply owner + 正式 Life runtime、脚本 Decision/内容替身，在全新的隔离根里准备一个
   带**真实阅读材料**（分页内容）和**真实私人事项文档**的实例；另建一件不同的作品 B。
2. 正式 START 阅读 A，逐页 CONTINUE 到这一版读完（``READING_EXHAUSTED``，真实停点）。
3. 脚本 Decision 显式使用本轮 offered 的暂停条件（``READING_MATERIAL_NEXT_VERSION``）选择
   PAUSE：真 LR-2 暂停、前台释放、真 LR-4 绑定条件且条件 PENDING。
4. 脚本 Decision 正式 START B（不同 Artifact）。B 执行、A 仍 PAUSED。
5. **本组关键差异：A 的 owner head 保持 PAUSE 时的 baseline，脚本不提交任何 A 新版本。**
6. 正式 ``AutonomousLifeRunner.run_due`` 先跑一次既有窗口（warm），让本窗落在
   ``last_check_at`` 之后；随后**连续两个** NOT_DUE 窗口仍跑既有维护：
   * 每个维护窗口 ``checked == 1``、``changed == 0``、``unchanged == 1``——
     同 head 被真实读过并比较过，但**没有**产生变更事件；
   * ``evaluated == 0``、``satisfied`` / ``emitted_eligibilities`` 为空、``errors`` 为空；
   * 条件仍 PENDING、**没有**任何资格、也没有 ``RESUME_ELIGIBLE`` 候选来源；
   * A 仍 PAUSED、B 仍 ACTIVE 且是唯一前台；认知替身计数前后不变。
7. 一个窄 spy 只包 owner 的 ``read_head``：断言每个 NOT_DUE 窗口确实发生一次真实 owner
   head 读取、读到的 ``version_id`` 就是 baseline（非空）。读到 ``None`` 不会被当成
   ``unchanged`` —— 那会由 ``errors`` 里的 ``OWNER_HEAD_UNREADABLE`` 暴露，本组也断言
   ``errors`` 为空，两者不能互相顶替。

边界（明示，不冒认）
--------------------
* Decision 与内容/阅读响应都是脚本替身；没有产品模型调用、没有网络、没有真实日常。
* 阅读材料、私人事项文档和作品 B 都是真实 Supply owner 状态；种子文本是 operator fixture。
* 本链证明的是机制接线（同 head 维护仍复查、零事件、零资格、零自动恢复），不是“真模型会
  自然这样选”，也不是 G1、不是 24h、不是 l 历史或任何旧实例证据。
* 时间用注入的 Life 时刻推进（只为让既有 check 窗口保持关闭），不睡墙钟、不改生产窗口配置。

运行：
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/reading_condition_same_head_runner_smoke.py
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
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_continuity as ac  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
import reading_condition_new_head_runner_smoke as new_head  # noqa: E402

from tests.test_one_day_life import Services  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_READING_CONDITION_SAME_HEAD_ROOT")
               or "/tmp/nyairo-reading-condition-same-head")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"

#: 复用“新 head”组的正式夹具文本与 helper（不修改那个模块）。
READING_CONTENT = new_head.READING_CONTENT
READING_SOURCE = new_head.READING_SOURCE
_check = new_head._check
_iso = new_head._iso
_conditions = new_head._conditions
_eligibilities = new_head._eligibilities
_activity = new_head._activity
_canonical = new_head._canonical
_active_ids = new_head._active_ids
_assert_single_execution = new_head._assert_single_execution
_open_eligibilities = new_head._open_eligibilities

#: 连续两个 NOT_DUE 窗口的注入偏移（秒）；两次都远小于既有 check 间隔。
NOT_DUE_OFFSETS = (60, 120)


class _ScriptedDecision(new_head._ScriptedDecision):
    """脚本 Decision：只在 AG-1 真正给的合法集合里选择，返回原始 JSON。"""

    model_id = "stub:reading-condition-same-head"
    model_version = "reading-condition-same-head-v1"


class _HeadReadSpy:
    """窄 spy：只代理 owner 的 head 读取并计数，返回值原样交给调用方。

    ``None`` 不被改写成任何“没变化”的答案——调用方（``cycle._owner_head``）会把它报成
    ``OWNER_HEAD_UNREADABLE``，本组的断言也会因为 ``errors`` 非空而红。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.reads: list[dict] = []

    def read_head(self, artifact_id):
        head = self._inner.read_head(artifact_id)
        version = ""
        if isinstance(head, dict):
            version = str(head.get("version_id") or "")
        self.reads.append({"artifact_id": str(artifact_id), "version_id": version,
                           "found": head is not None})
        return head

    def resume_point(self, artifact_id):
        return self._inner.resume_point(artifact_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def reads_since(self, index: int) -> list[dict]:
        return list(self.reads[int(index):])


def _head_for(reads: list[dict], artifact_id: str) -> list[dict]:
    wanted = new_head._bare_identity(artifact_id)
    return [row for row in reads
            if new_head._bare_identity(row.get("artifact_id")) == wanted]


def _assert_same_head_maintenance(record, waiting, *, reads, head_a1: str,
                                  artifact_id: str, where: str) -> None:
    """一个 NOT_DUE 窗口的完整同 head 断言（状态/计数/无资格/无自恢复）。"""

    _check(record.status == "NOT_DUE", "%s: the window was not NOT_DUE" % where,
           {"status": record.status, "reason": record.reason,
            "detail": str(record.detail or "")[:300],
            "waiting_state": waiting.get("state")})
    _check(str(waiting.get("state") or "") == "OK",
           "%s: the upkeep report is not OK" % where, waiting)
    _check(int(waiting.get("checked") or 0) == 1,
           "%s: the same head was not checked exactly once" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("changed") or 0) == 0,
           "%s: an unchanged head produced a change event" % where, waiting)
    _check(int(waiting.get("unchanged") or 0) == 1,
           "%s: the unchanged read was not counted as unchanged" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("evaluated") or 0) == 0,
           "%s: LR-4 evaluated an event on an unchanged head" % where, waiting)
    _check(list(waiting.get("satisfied") or []) == [],
           "%s: the condition was satisfied on the same head" % where, waiting)
    _check(list(waiting.get("emitted_eligibilities") or []) == [],
           "%s: an eligibility was emitted on the same head" % where, waiting)
    _check(list(waiting.get("errors") or []) == [],
           "%s: the upkeep reported an error" % where, waiting)
    _check(int(waiting.get("llm_calls") or 0) == 0,
           "%s: the upkeep spent a request" % where, waiting)
    _check(not waiting.get("auto_resumed"),
           "%s: the upkeep resumed something by itself" % where, waiting)
    wanted = _head_for(reads, artifact_id)
    _check(len(wanted) == 1,
           "%s: the owner head was not read exactly once for A" % where,
           {"reads": reads, "artifact_ref": str(artifact_id)})
    _check(bool(wanted[0].get("version_id")),
           "%s: the owner head read came back empty" % where, wanted[0])
    _check(str(wanted[0].get("version_id")) == head_a1,
           "%s: the owner head moved without a commit" % where,
           {"expected": head_a1, "observed": wanted[0].get("version_id")})


def run_smoke(root=None) -> dict:
    """跑完整一条链，返回 summary（只含 ID / 枚举 / 计数 / 原因码）。"""

    run_root = Path(root) if root is not None else RUN
    run_root.mkdir(parents=True, exist_ok=True)
    services = Services(run_root / "services")
    runtime = None
    cycle = None
    original_port = None
    summary: dict = {"root": str(run_root), "checks": {}, "limitations": [
        "Decision / content / reading response are scripted doubles; no product model was called",
        "the reading material, private-items document and work B are real owner state; "
        "the seed texts are operator fixtures",
        "A's owner head is deliberately left at its baseline: this group proves the same-head path",
        "the injected Life clock only keeps the existing check window closed; no wall-clock wait",
        "mechanism evidence only: not a natural model choice, not G1, not a 24h run",
    ]}
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:reading-condition-same-head",
            title="阅读条件同 head 冒烟",
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
            operation_key=lap.operation_key_for("reading-condition-same-head", "create-b", "b"))
        _check(created_b.applied and bool(created_b.artifact_id),
               "the owner did not create work B", created_b.to_dict())
        other_id = str(created_b.artifact_id)

        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecision(reading_id=reading_id, other_id=other_id)
        writer = new_head._ContentWriter()
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
        first = _turn("start_reading", "evt:reading-same-head:1", base)
        state = _canonical(runtime)
        active = _active_ids(state)
        _check(len(active) == 1, "round 1 did not leave exactly one ACTIVE Activity", state)
        activity_a = str(active[0])
        _assert_single_execution(runtime, activity_a, where="round1-start-reading")
        _check(first.get("adoption_status") == "APPLIED",
               "the reading START was not adopted",
               {"outcome": first.get("outcome"), "decision": first.get("decision"),
                "adoption_status": first.get("adoption_status"),
                "detail": str(first.get("detail") or "")[:300]})
        head_a1 = str((port.read_head(reading_id) or {}).get("version_id") or "")
        _check(bool(head_a1), "the reading material has no committed head", {})

        read_pages = 0
        for page in (2, 3):
            out = _turn("continue_reading", "evt:reading-same-head:read-%d" % page, base)
            situation = cycle.private_material_situation(refresh=True) or {}
            _check(out.get("outcome") in {"ACTED", "ALREADY_DONE"},
                   "reading page %d was not acted" % page,
                   {"outcome": out.get("outcome"), "decision": out.get("decision"),
                    "detail": str(out.get("detail") or "")[:300],
                    "state": situation.get("state")})
            read_pages += 1
        situation = cycle.private_material_situation(refresh=True) or {}
        _check(str(situation.get("state") or "") == "EXHAUSTED",
               "the material is not at its real stop point after the last page",
               {"state": situation.get("state"),
                "pages": situation.get("pages"),
                "position": situation.get("position")})
        versions_before = port.list_versions(reading_id)
        baseline_versions = int(versions_before.get("total_versions")
                                or versions_before.get("count") or 0)
        summary["read"] = {"pages_read": read_pages + 1,
                           "state": situation.get("state"),
                           "material_pages": situation.get("pages"),
                           "baseline_head": head_a1,
                           "baseline_versions": baseline_versions}

        # 2. 显式 offered head 依赖：PAUSE A -> 真 LR-2 暂停释放 + 真 LR-4 条件 PENDING
        second = _turn("pause_with_condition", "evt:reading-same-head:pause-a", base)
        offered = dict(cognition.last_offered.get("pause_condition") or {})
        _check(bool(offered.get("available")), "no pause condition was offered", offered)
        _check(str(offered.get("source") or "") == READING_SOURCE,
               "the offered condition is not the reading-material source", offered)
        _check(second.get("decision") == "PAUSE"
               and second.get("adoption_status") == "APPLIED",
               "the conditional PAUSE was not adopted",
               {"decision": second.get("decision"),
                "adoption_status": second.get("adoption_status"),
                "detail": str(second.get("detail") or "")[:300]})
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
        _check(_eligibilities(runtime) == {},
               "a PAUSE minted a resume eligibility", _eligibilities(runtime))
        summary["pause_a"] = {"decision": second.get("decision"),
                              "activity_a_status": paused_a.get("status"),
                              "condition_id": condition_id,
                              "condition_status": condition.get("status"),
                              "offered_source": offered.get("source"),
                              "eligibilities": 0}

        # 3. 脚本 Decision 正式 START B（不同 Artifact），A 保持 PAUSED
        third = _turn("start_other", "evt:reading-same-head:start-b", base)
        _check(third.get("decision") == "START" and third.get("adoption_status") == "APPLIED",
               "round 3 did not START work B",
               {"outcome": third.get("outcome"), "decision": third.get("decision"),
                "adoption_status": third.get("adoption_status"),
                "detail": str(third.get("detail") or "")[:300],
                "selection": cognition.last_selection})
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
        summary["start_b"] = {"decision": third.get("decision"),
                              "activity_b": activity_b,
                              "activity_a_status": ac.STATUS_PAUSED,
                              "foreground": activity_b}

        # 4. warm：先跑一次既有窗口入口，让本窗的 last_check_at 落在此刻之后。
        #    这一轮同样不许改变任何状态；它的维护报告作为同 head 的对照。
        cognition.policy = "no_action"
        warm = runner.run_due(act=True, now=base)
        warm_waiting = dict((warm.extra or {}).get("waiting") or {}) if warm.extra else {}
        _check(warm.status != "ERROR", "the warm-up window raised",
               {"status": warm.status, "detail": str(warm.detail or "")[:300],
                "waiting_state": warm_waiting.get("state")})
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "the warm-up window changed A", _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "the warm-up window changed B", _activity(runtime, activity_b))
        _assert_single_execution(runtime, activity_b, where="warm-up")
        summary["window_warm"] = {"status": warm.status,
                                  "waiting_state": warm_waiting.get("state"),
                                  "waiting_checked": warm_waiting.get("checked"),
                                  "waiting_changed": warm_waiting.get("changed"),
                                  "waiting_unchanged": warm_waiting.get("unchanged")}

        # 5. 连续两个 NOT_DUE 窗口：同 head、零事件、零资格、零自恢复、零认知。
        original_port = cycle.artifact_port
        spy = _HeadReadSpy(original_port)
        cycle.artifact_port = spy
        windows: list[dict] = []
        try:
            for index, offset in enumerate(NOT_DUE_OFFSETS, start=1):
                calls_before = cognition.calls
                reads_before = len(spy.reads)
                when = base + timedelta(seconds=offset)
                record = runner.run_due(act=True, now=when)
                calls_after = cognition.calls
                waiting = dict((record.extra or {}).get("waiting") or {})
                reads = spy.reads_since(reads_before)
                where = "not-due-%d" % index
                _assert_same_head_maintenance(
                    record, waiting, reads=reads, head_a1=head_a1,
                    artifact_id=reading_id, where=where)
                _check(calls_after == calls_before,
                       "%s: the NOT_DUE window spent a cognition request" % where,
                       {"before": calls_before, "after": calls_after})
                current_conditions = _conditions(runtime)
                current = current_conditions.get(condition_id) or {}
                _check(len(current_conditions) == 1,
                       "%s: the condition set changed" % where, current_conditions)
                _check(str(current.get("status") or "") == "PENDING",
                       "%s: the condition is no longer PENDING" % where, current)
                _check(_open_eligibilities(runtime) == [],
                       "%s: an OPEN eligibility appeared" % where, _eligibilities(runtime))
                _check(_eligibilities(runtime) == {},
                       "%s: an eligibility was written at all" % where,
                       _eligibilities(runtime))
                _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
                       "%s: A was resumed by itself" % where,
                       _activity(runtime, activity_a))
                _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
                       "%s: B stopped running" % where, _activity(runtime, activity_b))
                _assert_single_execution(runtime, activity_b, where=where)
                live_head = str((port.read_head(reading_id) or {}).get("version_id") or "")
                _check(live_head == head_a1,
                       "%s: the owner head moved during the window" % where,
                       {"expected": head_a1, "observed": live_head})
                windows.append({
                    "status": record.status,
                    "waiting_state": waiting.get("state"),
                    "checked": waiting.get("checked"),
                    "changed": waiting.get("changed"),
                    "unchanged": waiting.get("unchanged"),
                    "evaluated": waiting.get("evaluated"),
                    "satisfied": list(waiting.get("satisfied") or []),
                    "emitted_eligibilities": list(waiting.get("emitted_eligibilities") or []),
                    "errors": list(waiting.get("errors") or []),
                    "llm_calls": waiting.get("llm_calls"),
                    "auto_resumed": waiting.get("auto_resumed"),
                    "cognition_delta": calls_after - calls_before,
                    "owner_head_reads": len(reads),
                    "owner_head_value": (reads[0].get("version_id") if reads else None),
                    "condition_status": current.get("status"),
                    "eligibility_count": len(_eligibilities(runtime)),
                    "activity_a_status": ac.STATUS_PAUSED,
                    "activity_b_status": ac.STATUS_ACTIVE,
                    "foreground": activity_b,
                })
        finally:
            cycle.artifact_port = original_port
        summary["not_due_windows"] = windows

        # 6. 收尾复核：head 与版本数都没动（本组从未提交 A 新版本）
        versions_after = port.list_versions(reading_id)
        after_count = int(versions_after.get("total_versions")
                          or versions_after.get("count") or 0)
        head_after = str((port.read_head(reading_id) or {}).get("version_id") or "")
        _check(head_after == head_a1, "the reading head changed after the windows",
               {"expected": head_a1, "observed": head_after})
        _check(after_count == baseline_versions,
               "the reading version count changed after the windows",
               {"expected": baseline_versions, "observed": after_count})
        summary["same_head"] = {"baseline_head": head_a1,
                                "final_head": head_after,
                                "baseline_versions": baseline_versions,
                                "final_versions": after_count}

        summary["checks"] = {
            "reading_reached_real_stop": str(situation.get("state")) == "EXHAUSTED",
            "pause_bound_pending_condition": (len(conditions) == 1
                                              and str(condition.get("status")) == "PENDING"),
            "pause_released_foreground": paused_a.get("foreground_released") is True,
            "b_started_as_other_activity": activity_b != activity_a,
            "two_not_due_windows": len(windows) == len(NOT_DUE_OFFSETS),
            "same_head_checked_each_window": all(
                row["checked"] == 1 and row["changed"] == 0 and row["unchanged"] == 1
                for row in windows),
            "no_event_evaluated": all(row["evaluated"] == 0 for row in windows),
            "no_condition_satisfied": all(row["satisfied"] == [] for row in windows),
            "no_eligibility_emitted": all(row["emitted_eligibilities"] == []
                                          for row in windows),
            "no_upkeep_errors": all(row["errors"] == [] for row in windows),
            "zero_cognition_each_window": all(row["cognition_delta"] == 0 for row in windows),
            "zero_llm_calls_each_window": all(row["llm_calls"] == 0 for row in windows),
            "no_auto_resume": all(not row["auto_resumed"] for row in windows),
            "condition_stayed_pending": all(row["condition_status"] == "PENDING"
                                            for row in windows),
            "no_eligibility_at_all": all(row["eligibility_count"] == 0 for row in windows),
            "a_stayed_paused_b_stayed_active": all(
                row["activity_a_status"] == ac.STATUS_PAUSED
                and row["activity_b_status"] == ac.STATUS_ACTIVE for row in windows),
            "single_execution_throughout": True,
            "owner_head_never_moved": head_after == head_a1 and after_count == baseline_versions,
        }
        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        summary["model_calls"] = cognition.calls
        summary["writer_calls"] = writer.calls
        summary["reading_response_calls"] = writer.response_calls
        summary["ids"] = {"reading": reading_id, "other": other_id,
                          "activity_a": activity_a, "activity_b": activity_b,
                          "condition": condition_id}
        return summary
    finally:
        if runtime is not None and cycle is not None and original_port is not None:
            try:
                cycle.artifact_port = original_port
            except Exception:
                pass
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
        "root", "ids", "read", "pause_a", "start_b", "window_warm", "not_due_windows",
        "same_head", "checks", "model_calls", "writer_calls", "reading_response_calls",
        "limitations", "ok")},
        ensure_ascii=False, default=str, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
