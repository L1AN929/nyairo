#!/usr/bin/env python3
"""零模型真实链：owner 读不到 head 时，正式 runner 的 NOT_DUE 维护不猜“没变/已变”。

这条链只回答一件事（本组唯一目标）
----------------------------------
条件 PAUSE 之后，若读取 A 的 owner head **失败**，正式 ``AutonomousLifeRunner.run_due`` 的
NOT_DUE 维护是否把失败当成明确错误，而不是当成 ``unchanged`` / ``changed``。

1. 真实 Supply owner + 正式 Life runtime、脚本 Decision/内容替身，在全新隔离根里准备带
   **真实阅读材料**（分页内容）与**真实私人事项文档**的实例；另建一件不同的作品 B。
2. 正式 START 阅读 A，逐页 CONTINUE 到这一版读完（``READING_EXHAUSTED``，真实停点）。
3. 脚本 Decision 显式使用本轮 offered 的 ``READING_MATERIAL_NEXT_VERSION`` 选择 PAUSE：
   真 LR-2 暂停释放前台、真 LR-4 绑定条件且 PENDING。
4. 脚本 Decision 正式 START B。B ACTIVE 持前台，A 仍 PAUSED，资格为空。
5. 正式 ``runner.run_due`` 先 warm 一次既有窗口，让后续窗口落在 ``last_check_at`` 之后。
6. **本组关键差异**：两个 NOT_DUE 窗口分别注入两种 owner head 读失败形态，
   只针对 A 的 artifact（其它 artifact/其它请求透传真 owner，不替换 store、不手造 condition）：
   * ``none``      —— ``read_head`` 返回 ``None``；生产 ``_owner_head`` 会退到
     ``resume_point``，真实不可读 owner 同样答不出，所以该回退也抛真
     ``ArtifactOwnerRefused``（真实端口拒读时用的同一个异常类型）；
   * ``oserror``   —— ``read_head`` 抛 ``OSError``（隔离注入，不是真撤权、不是停别人的服务）。
   两个窗口都必须：``checked == 1``、``changed == 0``、``unchanged == 0``、
   ``evaluated == 0``、``satisfied`` / ``emitted_eligibilities`` 为空、
   ``errors`` 明确含 ``OWNER_HEAD_UNREADABLE``、``llm_calls == 0``、未自动恢复；
   条件仍 PENDING、资格存储整体为空、A 仍 PAUSED、B 仍 ACTIVE 且唯一前台。
   外层 ``waiting["state"]`` 仍可能报 ``OK``——本组把它和 ``errors`` 一起记录，
   **不**把“外层 OK”读成“没有错误”。
7. **恢复负控**：撤掉注入（换回真 owner 端口）后，下一 NOT_DUE 窗口真实读回 baseline：
   ``errors`` 为空、``unchanged == 1``、``changed == 0``、条件仍 PENDING、无资格；
   并且 A 的 head 与版本数仍等于 baseline——证明注入没有改 owner 状态、没有留下假事件。

边界（明示，不冒认）
--------------------
* Decision 与内容/阅读响应都是脚本替身；没有产品模型调用、没有网络、没有真实日常。
* 阅读材料、私人事项文档、作品 B 都是真实 Supply owner 状态；种子文本是 operator fixture。
* 故障是**测试内的窄注入**，不是真实权限撤销、不是跨备份、不是停服务；不证明 l 历史。
* 本链证明的是机制接线（读失败 → 明确错误、零事件、零资格、零自动恢复），不是“真模型会
  自然这样选”，也不是 G1、不是 24h。
* 时间只用注入的 Life 时刻保持既有 check 窗口关闭，不睡墙钟、不改生产窗口配置。

运行：
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/reading_condition_read_failure_runner_smoke.py
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

SCRATCH = Path(os.environ.get("NYAIRO_READING_CONDITION_READ_FAILURE_ROOT")
               or "/tmp/nyairo-reading-condition-read-failure")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"

#: 复用“新 head / 同 head”两组已验证的正式夹具（不修改那些模块）。
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

#: 两个故障窗口的注入偏移（秒）：都远小于既有 check 间隔，保持 NOT_DUE。
FAULT_WINDOWS = (("none", 60), ("oserror", 120))
RECOVERY_OFFSET = 180

OWNER_HEAD_UNREADABLE = "OWNER_HEAD_UNREADABLE"


class _ScriptedDecision(new_head._ScriptedDecision):
    """脚本 Decision：只在 AG-1 真正给的合法集合里选择，返回原始 JSON。"""

    model_id = "stub:reading-condition-read-failure"
    model_version = "reading-condition-read-failure-v1"


class _OwnerHeadFault:
    """窄故障注入：只让 A 的 owner head 读取失败，其它 artifact/请求透传真 owner。

    ``none``    形态：``read_head`` 返回 ``None``；生产 ``_owner_head`` 会退到
    ``resume_point``，真实不可读的 owner 在那里同样答不出，因此回退也抛真
    ``ArtifactOwnerRefused``（真实端口拒读用的同一个异常类型，不是手造 condition）。

    ``oserror`` 形态：``read_head`` 抛 ``OSError``；``resume_point`` 根本不会被调用。

    不改写任何“没变化”的返回值：调用方拿到的是 ``None`` 或异常，维护必须自己报错。
    """

    def __init__(self, inner, artifact_id, mode: str) -> None:
        self._inner = inner
        self._wanted = new_head._bare_identity(artifact_id)
        self.mode = str(mode)
        self.attempts: list[dict] = []

    def _is_target(self, artifact_id) -> bool:
        return new_head._bare_identity(artifact_id) == self._wanted

    def read_head(self, artifact_id):
        if self._is_target(artifact_id):
            if self.mode == "none":
                self.attempts.append({"op": "read_head", "mode": self.mode,
                                      "answer": "none"})
                return None
            self.attempts.append({"op": "read_head", "mode": self.mode,
                                  "answer": "raised:OSError"})
            raise OSError("test-only injected owner head read failure")
        return self._inner.read_head(artifact_id)

    def resume_point(self, artifact_id):
        if self.mode == "none" and self._is_target(artifact_id):
            self.attempts.append({"op": "resume_point", "mode": self.mode,
                                  "answer": "raised:ArtifactOwnerRefused"})
            raise lap.ArtifactOwnerRefused(
                "OWNER_READ_FAILED", OWNER_HEAD_UNREADABLE,
                "test-only injected unreadable owner head")
        return self._inner.resume_point(artifact_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _HeadReadCounter:
    """只读透传 spy：记录恢复窗口里真实 owner head 读取的值，不改写返回值。"""

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

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _waiting_of(record) -> dict:
    return dict((record.extra or {}).get("waiting") or {}) if record.extra else {}


def _assert_unreadable_maintenance(record, waiting, *, fault, where: str) -> None:
    """一个读失败窗口的完整断言：明确错误、零事件、零资格、零自恢复。"""

    _check(record.status == "NOT_DUE", "%s: the window was not NOT_DUE" % where,
           {"status": record.status, "reason": record.reason,
            "detail": str(record.detail or "")[:300],
            "waiting_state": waiting.get("state"),
            "errors": list(waiting.get("errors") or [])})
    # 外层 state 可能仍是 OK；本组把它和 errors 一起记录，绝不把外层 OK 读成没有错误。
    _check(str(waiting.get("state") or "") == "OK",
           "%s: the upkeep report is not OK" % where, waiting)
    _check(int(waiting.get("checked") or 0) == 1,
           "%s: the failed owner head was not checked exactly once" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("unchanged") or 0) == 0,
           "%s: an unreadable owner head was counted as unchanged" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("changed") or 0) == 0,
           "%s: an unreadable owner head was counted as changed" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("evaluated") or 0) == 0,
           "%s: LR-4 evaluated an event without a readable head" % where, waiting)
    _check(list(waiting.get("satisfied") or []) == [],
           "%s: a condition was satisfied without a readable head" % where, waiting)
    _check(list(waiting.get("emitted_eligibilities") or []) == [],
           "%s: an eligibility was emitted without a readable head" % where, waiting)
    errors = [str(row) for row in (waiting.get("errors") or [])]
    _check(any(row.startswith(OWNER_HEAD_UNREADABLE) for row in errors),
           "%s: the unreadable owner head was not reported as an explicit error" % where,
           {"errors": errors, "waiting": waiting})
    _check(int(waiting.get("llm_calls") or 0) == 0,
           "%s: the upkeep spent a request" % where, waiting)
    _check(not waiting.get("auto_resumed"),
           "%s: the upkeep resumed something by itself" % where, waiting)

    if fault.mode == "none":
        shape = [{"op": "read_head", "answer": "none"},
                 {"op": "resume_point", "answer": "raised:ArtifactOwnerRefused"}]
    else:
        shape = [{"op": "read_head", "answer": "raised:OSError"}]
    observed = [{"op": row.get("op"), "answer": row.get("answer")}
                for row in fault.attempts]
    _check(observed == shape,
           "%s: the injected head-read fault was not exercised in its declared shape" % where,
           {"mode": fault.mode, "expected": shape, "observed": observed})


def _assert_readable_maintenance(record, waiting, *, head_a1: str, real_head: str,
                                 where: str) -> None:
    """恢复窗口：真实 owner 读回 baseline，errors 空、unchanged 1、无资格、无事件。"""

    _check(record.status == "NOT_DUE", "%s: the window was not NOT_DUE" % where,
           {"status": record.status, "reason": record.reason,
            "detail": str(record.detail or "")[:300]})
    _check(str(waiting.get("state") or "") == "OK",
           "%s: the upkeep report is not OK" % where, waiting)
    _check(int(waiting.get("checked") or 0) == 1,
           "%s: the owner head was not checked exactly once" % where, waiting)
    _check(int(waiting.get("unchanged") or 0) == 1,
           "%s: the readable baseline head was not counted as unchanged" % where,
           {"checked": waiting.get("checked"), "unchanged": waiting.get("unchanged"),
            "changed": waiting.get("changed"), "errors": waiting.get("errors")})
    _check(int(waiting.get("changed") or 0) == 0,
           "%s: the baseline head produced a change event" % where, waiting)
    _check(int(waiting.get("evaluated") or 0) == 0,
           "%s: LR-4 evaluated an event on the baseline head" % where, waiting)
    _check(list(waiting.get("satisfied") or []) == [],
           "%s: a condition was satisfied after the fault was removed" % where, waiting)
    _check(list(waiting.get("emitted_eligibilities") or []) == [],
           "%s: an eligibility was emitted after the fault was removed" % where, waiting)
    _check(list(waiting.get("errors") or []) == [],
           "%s: the recovered read still reported an error" % where,
           {"errors": waiting.get("errors")})
    _check(int(waiting.get("llm_calls") or 0) == 0,
           "%s: the upkeep spent a request" % where, waiting)
    _check(not waiting.get("auto_resumed"),
           "%s: the upkeep resumed something by itself" % where, waiting)
    _check(bool(real_head) and real_head == head_a1,
           "%s: the real owner head is no longer the baseline" % where,
           {"expected": head_a1, "observed": real_head})


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
        "the head-read failure is a narrow in-test injection for artifact A only, not a real "
        "permission revoke, not a stopped service and not a cross-backup restore",
        "the injected Life clock only keeps the existing check window closed; no wall-clock wait",
        "mechanism evidence only: not a natural model choice, not G1, not a 24h run, "
        "not the l instance history",
    ]}
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:reading-condition-read-failure",
            title="阅读条件读失败冒烟",
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
            operation_key=lap.operation_key_for("reading-condition-read-failure", "create-b", "b"))
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
        first = _turn("start_reading", "evt:reading-read-failure:1", base)
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
            out = _turn("continue_reading", "evt:reading-read-failure:read-%d" % page, base)
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
        second = _turn("pause_with_condition", "evt:reading-read-failure:pause-a", base)
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
        third = _turn("start_other", "evt:reading-read-failure:start-b", base)
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
        cognition.policy = "no_action"
        warm = runner.run_due(act=True, now=base)
        warm_waiting = _waiting_of(warm)
        _check(warm.status != "ERROR", "the warm-up window raised",
               {"status": warm.status, "detail": str(warm.detail or "")[:300],
                "waiting_state": warm_waiting.get("state")})
        _check(int(warm_waiting.get("unchanged") or 0) in {0, 1},
               "the warm-up window read an unexpected owner head", warm_waiting)
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

        # 5. 两个 NOT_DUE 窗口：A 的 owner head 读失败（两种形态）不得被当成 unchanged/changed。
        original_port = cycle.artifact_port
        fault_windows: list[dict] = []
        for mode, offset in FAULT_WINDOWS:
            where = "not-due-%s" % mode
            fault = _OwnerHeadFault(original_port, reading_id, mode)
            calls_before = cognition.calls
            waiting: dict = {}
            record = None
            try:
                cycle.artifact_port = fault
                record = runner.run_due(act=True, now=base + timedelta(seconds=offset))
            finally:
                cycle.artifact_port = original_port
            calls_after = cognition.calls
            waiting = _waiting_of(record)
            _assert_unreadable_maintenance(record, waiting, fault=fault, where=where)
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
                   "%s: an eligibility was written at all" % where, _eligibilities(runtime))
            _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
                   "%s: A was resumed by itself" % where, _activity(runtime, activity_a))
            _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
                   "%s: B stopped running" % where, _activity(runtime, activity_b))
            _assert_single_execution(runtime, activity_b, where=where)
            live_head = str((port.read_head(reading_id) or {}).get("version_id") or "")
            _check(live_head == head_a1,
                   "%s: the owner head moved during the window" % where,
                   {"expected": head_a1, "observed": live_head})
            fault_windows.append({
                "fault_mode": mode,
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
                "owner_head_attempts": list(fault.attempts),
                "condition_status": current.get("status"),
                "eligibility_count": len(_eligibilities(runtime)),
                "activity_a_status": ac.STATUS_PAUSED,
                "activity_b_status": ac.STATUS_ACTIVE,
                "foreground": activity_b,
            })
        summary["fault_windows"] = fault_windows

        # 6. 恢复负控：撤掉注入，下一 NOT_DUE 窗口真实读回 baseline。
        #    这里用一个只读透传 spy 证明“确实读了真 owner”，并记录读回的值。
        counter = _HeadReadCounter(original_port)
        calls_before = cognition.calls
        recovered = None
        try:
            cycle.artifact_port = counter
            recovered = runner.run_due(act=True, now=base + timedelta(seconds=RECOVERY_OFFSET))
        finally:
            cycle.artifact_port = original_port
        calls_after = cognition.calls
        recovery_waiting = _waiting_of(recovered)
        real_head_after = str((port.read_head(reading_id) or {}).get("version_id") or "")
        _assert_readable_maintenance(recovered, recovery_waiting, head_a1=head_a1,
                                     real_head=real_head_after, where="recovery")
        _check(calls_after == calls_before,
               "recovery: the NOT_DUE window spent a cognition request",
               {"before": calls_before, "after": calls_after})
        reads_for_a = [row for row in counter.reads
                       if new_head._bare_identity(row.get("artifact_id"))
                       == new_head._bare_identity(reading_id)]
        _check(len(reads_for_a) == 1,
               "recovery: the real owner head was not read exactly once for A",
               {"reads": counter.reads})
        _check(str(reads_for_a[0].get("version_id") or "") == head_a1,
               "recovery: the recovered read did not return the baseline head",
               {"expected": head_a1, "observed": reads_for_a[0]})
        recovered_conditions = _conditions(runtime)
        recovered_condition = recovered_conditions.get(condition_id) or {}
        _check(str(recovered_condition.get("status") or "") == "PENDING",
               "recovery: the condition is no longer PENDING", recovered_condition)
        _check(_eligibilities(runtime) == {},
               "recovery: an eligibility was written after the fault was removed",
               _eligibilities(runtime))
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "recovery: A was resumed by itself", _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "recovery: B stopped running", _activity(runtime, activity_b))
        _assert_single_execution(runtime, activity_b, where="recovery")
        summary["recovery"] = {
            "status": recovered.status,
            "waiting_state": recovery_waiting.get("state"),
            "checked": recovery_waiting.get("checked"),
            "changed": recovery_waiting.get("changed"),
            "unchanged": recovery_waiting.get("unchanged"),
            "evaluated": recovery_waiting.get("evaluated"),
            "satisfied": list(recovery_waiting.get("satisfied") or []),
            "emitted_eligibilities": list(recovery_waiting.get("emitted_eligibilities") or []),
            "errors": list(recovery_waiting.get("errors") or []),
            "llm_calls": recovery_waiting.get("llm_calls"),
            "auto_resumed": recovery_waiting.get("auto_resumed"),
            "cognition_delta": calls_after - calls_before,
            "owner_head_reads": len(reads_for_a),
            "owner_head_value": str(reads_for_a[0].get("version_id") or ""),
            "condition_status": recovered_condition.get("status"),
            "eligibility_count": len(_eligibilities(runtime)),
            "activity_a_status": ac.STATUS_PAUSED,
            "activity_b_status": ac.STATUS_ACTIVE,
            "foreground": activity_b,
        }

        # 7. 收尾复核：head 与版本数都没动（本组从未提交 A 新版本，注入也不写 owner）
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
            "two_fault_windows": len(fault_windows) == len(FAULT_WINDOWS),
            "fault_forms_exercised": [row["fault_mode"] for row in fault_windows]
            == [mode for mode, _offset in FAULT_WINDOWS],
            "checked_once_each_window": all(row["checked"] == 1 for row in fault_windows
                                            + [summary["recovery"]]),
            "no_unchanged_on_failure": all(row["unchanged"] == 0 for row in fault_windows),
            "no_changed_on_failure": all(row["changed"] == 0 for row in fault_windows),
            "unreadable_reported_as_error": all(
                any(str(code).startswith(OWNER_HEAD_UNREADABLE) for code in row["errors"])
                for row in fault_windows),
            "no_event_on_failure": all(row["evaluated"] == 0 for row in fault_windows),
            "no_condition_satisfied_on_failure": all(row["satisfied"] == []
                                                     for row in fault_windows),
            "no_eligibility_on_failure": all(row["emitted_eligibilities"] == []
                                             and row["eligibility_count"] == 0
                                             for row in fault_windows),
            "zero_cognition_each_window": all(row["cognition_delta"] == 0
                                              for row in fault_windows
                                              + [summary["recovery"]]),
            "no_auto_resume": all(not row["auto_resumed"] for row in fault_windows
                                  + [summary["recovery"]]),
            "condition_stayed_pending": all(row["condition_status"] == "PENDING"
                                            for row in fault_windows
                                            + [summary["recovery"]]),
            "a_stayed_paused_b_stayed_active": all(
                row["activity_a_status"] == ac.STATUS_PAUSED
                and row["activity_b_status"] == ac.STATUS_ACTIVE for row in fault_windows
                + [summary["recovery"]]),
            "single_execution_throughout": True,
            "recovery_read_real_baseline": summary["recovery"]["owner_head_reads"] == 1
            and summary["recovery"]["owner_head_value"] == head_a1,
            "recovery_no_errors": summary["recovery"]["errors"] == [],
            "recovery_unchanged_once": summary["recovery"]["unchanged"] == 1
            and summary["recovery"]["changed"] == 0,
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
        "root", "ids", "read", "pause_a", "start_b", "window_warm", "fault_windows",
        "recovery", "same_head", "checks", "model_calls", "writer_calls",
        "reading_response_calls", "limitations", "ok")},
        ensure_ascii=False, default=str, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
