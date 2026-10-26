#!/usr/bin/env python3
"""v0.5 零模型真 owner 冒烟：她自己定下的「稍后」到点后被正式重新考虑。

真实件：Supply owner（canonical db + Unix socket）、ProductionLifeRuntime（lease）、
``AutonomousLifeCycle``、``AutonomousLifeRunner``（正式 check-window）、``FutureItems`` 真实
持久存储、AG-0 正式候选源、AG-1 原始 JSON 校验、AG-2 采纳、LR-2/LR-4。
替身只有：Decision 回答（脚本 raw JSON）与内容回答（StubWriter）。0 产品模型、0 外网。

同一隔离根、同一进程，每个窗口都走正式 ``AutonomousLifeRunner.run_due``：

1. 正控：正式 START 建立前台活动并写出一版 → DEFER+``later_item``（带作品对象）保存真实事项；
   到期前一个窗口是 ``QUIET``（0 请求、0 内容写）；到点窗口里事项进入 material/指纹/正式
   ``FUTURE_ITEM_DUE`` 候选；同一到期集合的下一个窗口再次 ``QUIET``（不自激、不重复）；
   ``NO_ACTION`` 真实提交；取消/改期后下一窗口移除/延后，不复活。
2. 搁置/返回：真实 WAIT（时间条件 + 释放前台）→ 到期由 LR-4 自己发 OPEN；前台被「别事」
   占用时该资格不被抢占、不被消费；``NO_ACTION`` 也不消费它；把「别事」也搁置释放前台后，
   新 Decision 显式 RESUME 同一个 Activity，资格恰好消费一次。
3. 无对象事项只在既有项目选项里出现（不产生 ``FUTURE_ITEM_DUE`` 候选），选中它由真实 owner
   创建新作品（「别事」），事项本身没有被自动执行。
4. 重开同根：关闭 runtime（释放 lease）后在同一进程重装配，事项/状态从磁盘读回，不丢不重放。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/v05_due_reconsideration_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SUBJECT = "test:v05-due-reconsideration"
T0 = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)

#: 与生产 ``run_service`` 的默认有界等待一致：先让在途请求结束，再动 socket 文件。
DRAIN_TIMEOUT_SECONDS = 10.0

CHECKS: list[tuple[str, bool, object]] = []


#: 她自己的自由文本字段：诊断输出只给 ID/枚举/计数/时刻，不回显这些内容。
_TEXT_KEYS = frozenset({"title", "seed_text", "note", "notes", "content", "excerpt",
                        "short_rationale", "reason", "body", "text", "rationale"})


def _redact(value):
    if isinstance(value, dict):
        return {key: ("<text>" if str(key) in _TEXT_KEYS else _redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-70s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(_redact(detail), ensure_ascii=False, default=str)[:900])


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(moment: datetime) -> str:
    return moment.isoformat()


# ---------------------------------------------------------------------------------------------
# 隔离夹具：真实 Supply owner 与真实实例准备
# ---------------------------------------------------------------------------------------------


class Clock:
    """业务时钟的可注入替身：真实预算/权限时钟不被快进。"""

    def __init__(self, moment: datetime) -> None:
        self.now = moment

    def __call__(self) -> datetime:
        return self.now


def _start_services(root: Path):
    from tests.test_one_day_life import Services

    return Services(Path(root))


def _stop_services(services) -> dict:
    """正规关闭自己的 owner：shutdown → server_close → drain → prepare_socket_path → close。

    只接受本夹具自己新建的隔离 socket 路径；不裸 unlink、不回收旧 socket。
    """

    from lifesupply.service.life_supply import prepare_socket_path

    socket_path = Path(services.supply_path)
    expected = Path(services.root) / "life-supply.sock"
    if socket_path != expected:
        raise RuntimeError("REFUSING_TO_STOP_UNKNOWN_SOCKET:%s" % socket_path)
    server = services.server
    server.shutdown()
    server.server_close()
    drained = server.drain(DRAIN_TIMEOUT_SECONDS)
    try:
        socket_handling = prepare_socket_path(socket_path)
    finally:
        services.service.close()
    return {"socket_path": str(socket_path), "drained": bool(drained),
            "socket_handling": str(socket_handling or "")}


def _fresh_root() -> Path:
    base = Path(tempfile.gettempdir()) / "nyairo-v05-due"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = base / ("%s-%s" % (stamp, os.getpid()))
    index = 1
    while root.exists():
        root = base / ("%s-%s-%d" % (stamp, os.getpid(), index))
        index += 1
    root.mkdir(parents=True)
    return root


def prepare_fixture(root: Path) -> Path:
    """世界包 + ``odl.prepare`` 的隔离实例；owner 随后正规关闭，不常驻。"""

    import one_day_life as odl
    from components.worldgen import schema
    from components.worldgen.tests._setup import make_valid_docs
    from tests.test_one_day_life import ENV_GATES

    package_dir = root / "package"
    schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
    instance = root / "instance"
    services = _start_services(root / "supply")
    try:
        with mock.patch.dict(os.environ, ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=services.supply_path,
                                   governance_socket=services.gov_path, subject=SUBJECT,
                                   title="v0.5 due reconsideration",
                                   world_package=package_dir)
        if not prepared.get("ok"):
            raise SystemExit("prepare failed: %r" % (prepared,))
    finally:
        _stop_services(services)
    return instance


def open_chain(root: Path, services, clock: Clock, cognition):
    """在同一根上装配正式 runtime/assembly（关闭后可以再次调用）。"""

    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from tests.test_one_day_life import StubWriter

    instance = root / "instance"
    data = odl.read_instance(instance)
    env = odl.instance_environment(instance)
    env.update({
        "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
        "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
        "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
        "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        # 逻辑分钟的检查窗：每个窗口由本脚本的注入时钟推进，不是新的后台计时器。
        "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES": "1",
    })
    port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
        install_root=instance / "life", artifact_action_adapter=adapter)
    writer = StubWriter()
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=odl.life_state_root(instance),
        writer=writer, enabled=True, clock=clock)
    if assembled["state"] != assembly.READY:
        raise SystemExit("assembly not READY: %r" % (assembled,))
    return SimpleNamespace(root=root, instance=instance, services=services, data=data,
                           env=env, port=port, runtime=runtime, assembled=assembled,
                           cycle=assembled["cycle"], runner=assembled["runner"],
                           cognition=cognition, writer=writer, clock=clock)


def close_chain(chain) -> dict:
    report = {"runtime_closed": False, "lease_released": False}
    try:
        chain.runtime.close()
        report["runtime_closed"] = True
        report["lease_released"] = not bool(getattr(chain.runtime.lease, "held", True))
    except Exception as exc:  # noqa: BLE001 - printed, never hidden
        print("  runtime close error:", type(exc).__name__, str(exc)[:200])
    return report


# ---------------------------------------------------------------------------------------------
# Decision 替身：只回答原始 JSON，真实 AG-1 校验、AG-2 采纳、AR-0 写
# ---------------------------------------------------------------------------------------------


class ScriptedCognition:
    """The decision model, scripted: raw JSON only; real AG-1 validates it."""

    model_id = "stub:v05-due-reconsideration"
    model_version = "v05-due-reconsideration-1"

    def __init__(self) -> None:
        self.call_count = 0
        self.policy = "no_action"
        self.payload: dict = {}
        self.seed_artifact = ""
        self.last_candidates: list = []
        self.last_picked_ref = ""
        self.last_picked_kind = ""

    @staticmethod
    def _pick(candidates, predicate):
        for candidate in candidates:
            if predicate(candidate):
                return candidate
        return None

    def _chosen(self, candidate) -> str:
        self.last_picked_ref = str(candidate.get("source_ref") or "")
        self.last_picked_kind = str(candidate.get("candidate_kind") or "")
        return str(candidate.get("candidate_id") or "")

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        self.last_candidates = candidates
        self.last_picked_ref = ""
        self.last_picked_kind = ""
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]

        def legal(candidate, word):
            return word in (candidate.get("legal_decisions") or [])

        raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
               "reason_codes": reason, "short_rationale": "stub: quiet",
               "deferred_candidate_ids": []}
        if self.policy == "start_seed":
            chosen = self._pick(
                candidates,
                lambda c: "closeout:" not in "".join(str(r) for r in (c.get("target_refs") or []))
                and self.seed_artifact in str(c.get("source_ref") or "")
                and legal(c, "START"))
            if chosen is not None:
                raw = {"decision": "START", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: continue the work",
                       "deferred_candidate_ids": []}
        elif self.policy == "start_project":
            chosen = self._pick(
                candidates,
                lambda c: str(c.get("source_ref") or "").startswith("popt:")
                and legal(c, "START"))
            if chosen is not None:
                raw = {"decision": "START", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: make a work of it",
                       "deferred_candidate_ids": [],
                       "future_item_actions": list(self.payload.get("actions") or [])}
        elif self.policy == "defer_propose":
            raw = {"decision": "DEFER", "selected_candidate_id": None,
                   "reason_codes": reason, "short_rationale": "stub: later",
                   "deferred_candidate_ids": [], "later_item": dict(self.payload)}
        elif self.policy == "no_action_propose":
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": reason, "short_rationale": "stub: not now",
                   "deferred_candidate_ids": [], "later_item": dict(self.payload)}
        elif self.policy == "no_action_actions":
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": reason, "short_rationale": "stub: adjust my later",
                   "deferred_candidate_ids": [],
                   "future_item_actions": list(self.payload.get("actions") or [])}
        elif self.policy == "park":
            chosen = self._pick(
                candidates,
                lambda c: c.get("candidate_kind") == "CONTINUE_CURRENT" and legal(c, "WAIT"))
            if chosen is not None:
                raw = {"decision": "WAIT", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: set it down",
                       "deferred_candidate_ids": [],
                       "later_item": {"title": "回头接着把这一段写完",
                                      "in_minutes": int(self.payload.get("in_minutes") or 2),
                                      "park_current": True}}
        elif self.policy == "resume":
            chosen = self._pick(
                candidates,
                lambda c: c.get("candidate_kind") == "RESUME_ACTIVITY" and legal(c, "RESUME"))
            if chosen is not None:
                raw = {"decision": "RESUME", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: come back to it",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:due-reconsideration:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


# ---------------------------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------------------------


def window(chain, moment: datetime, policy: str = "no_action", payload=None) -> dict:
    chain.clock.now = moment
    chain.cognition.policy = policy
    chain.cognition.payload = dict(payload or {})
    record = chain.runner.run_due(act=True)
    return record.to_dict()


def parts(record: dict) -> dict:
    return dict(((record.get("extra") or {}).get("reconsideration") or {}).get("parts") or {})


def head_version(chain, artifact_id) -> str:
    point = chain.port.resume_point(str(artifact_id or ""))
    return str(getattr(point, "head_version_id", "") or "")


def version_count(chain, artifact_id) -> int:
    return int(chain.port.list_versions(str(artifact_id or ""), limit=50)["total_versions"])


def open_items(chain) -> list:
    return [row.to_dict() for row in chain.cycle.future_items.open_items()]


def all_items(chain) -> dict:
    return {row.item_ref: row.status for row in chain.cycle.future_items.items()}


def extra_items(chain) -> dict:
    """本轮正式来源缓存里的 fitem/popt 额外机会（真实 discover 的产物）。"""

    return dict(getattr(chain.cycle.artifact_source, "last_extra_items", {}) or {})


def option_bases(chain) -> list:
    return [str(row.get("basis_ref") or "") for row in (chain.cycle.last_project_options or [])]


def activity_status(chain, activity_ref) -> str:
    row = chain.runtime.activity_read.get_activity(activity_ref) or {}
    return str(row.get("status") or "")


def foreground(chain) -> str:
    return str(chain.runtime.canonical_state().get("foreground_activity_ref") or "")


def eligibilities(chain, activity_ref, status=None) -> list:
    rows = [row for row in (chain.runtime.waiting_coordinator.list_eligibilities() or [])
            if row.get("activity_id") == activity_ref]
    if status is not None:
        rows = [row for row in rows if row.get("status") == status]
    return rows


def writes(chain) -> list:
    return list(chain.cycle.last_future_item_writes or [])


# ---------------------------------------------------------------------------------------------
# 阶段 1：带作品对象的稍后项 —— 到期前安静、到点进候选、同一集合再次安静、取消/改期
# ---------------------------------------------------------------------------------------------


def phase_items(chain) -> dict:
    out: dict = {"phase": "items"}
    seed = str(chain.data["artifact_id"])
    head0 = head_version(chain, seed)

    first = window(chain, at(0), "start_seed")
    activity_ref = str(first.get("activity_ref") or "")
    out["activity_ref"] = activity_ref
    check("1.1 official START is adopted and one version is written",
          first.get("outcome") == "ACTED", first)
    check("1.2 the foreground Activity is really ACTIVE",
          activity_status(chain, activity_ref) == "ACTIVE", activity_status(chain, activity_ref))
    check("1.3 the new version came back from the real owner",
          head_version(chain, seed) != head0, {"head": head_version(chain, seed)})

    deferred = window(chain, at(2), "defer_propose",
                      {"title": "回头把这一段再改一遍", "in_minutes": 4,
                       "source_refs": ["artifact:%s" % seed]})
    saved = open_items(chain)
    item_a = saved[0] if saved else {}
    out["item_a"] = str(item_a.get("item_ref") or "")
    check("1.4 DEFER+later_item is committed by real AG-1 and stored",
          deferred.get("outcome") == "DEFERRED" and len(saved) == 1, saved)
    check("1.5 the item records its due time, origin and artifact target",
          item_a.get("due_at") == iso(at(6))
          and item_a.get("origin") == "MODEL_PROPOSAL"
          and ("artifact:%s" % seed) in (item_a.get("source_refs") or []), item_a)
    writer_after = int(chain.writer.call_count)
    head_after = head_version(chain, seed)

    before = window(chain, at(3))
    check("1.6 before due the window is QUIET (0 requests, 0 content writes)",
          before.get("status") == "QUIET" and int(before.get("llm_calls") or 0) == 0
          and int(chain.writer.call_count) == writer_after
          and head_version(chain, seed) == head_after, before)
    check("1.7 before due the item is not due and no candidate is built for it",
          chain.cycle.future_items.due(iso(at(3))) == []
          and chain.cycle.due_future_items(iso(at(3))) == []
          and out["item_a"] not in extra_items(chain),
          {"extra": sorted(extra_items(chain))})

    due_round = window(chain, at(7), "no_action_propose",
                       {"title": "把刚才那个念头写成一段", "in_minutes": 2})
    item_b = next((row for row in open_items(chain)
                   if row.get("item_ref") != out["item_a"]), {})
    out["item_b"] = str(item_b.get("item_ref") or "")
    due_parts = parts(due_round)
    check("1.8 the due window really considered (not QUIET) and committed NO_ACTION",
          due_round.get("status") == "NO_ACTION" and due_round.get("decision") == "NO_ACTION",
          due_round)
    check("1.9 the due item is part of the situation fingerprint",
          out["item_a"] in str(due_parts.get("due_items") or ""), due_parts)
    check("1.10 a due item that names an object becomes a formal candidate",
          out["item_a"] in extra_items(chain)
          and ("artifact:%s" % seed) in (extra_items(chain).get(out["item_a"], {})
                                         .get("target_refs") or []), extra_items(chain))
    check("1.11 an item without an object is not due, names no target, and stays a later item",
          out["item_b"] not in str(due_parts.get("due_items") or "")
          and not (item_b.get("source_refs") or []) and bool(out["item_b"]), item_b)
    check("1.12 NO_ACTION wrote no content at all",
          int(chain.writer.call_count) == writer_after
          and head_version(chain, seed) == head_after,
          {"writer_calls": chain.writer.call_count})

    again = window(chain, at(8))
    check("1.13 the same due set stays quiet on the next window (no self-excitation)",
          again.get("status") == "QUIET" and int(again.get("llm_calls") or 0) == 0
          and len(open_items(chain)) == 2, again)

    window(chain, at(9), "no_action_actions",
           {"actions": [{"item_ref": out["item_a"], "op": "cancel", "reason": "先不做了"},
                        {"item_ref": out["item_b"], "op": "reschedule", "in_minutes": 5}]})
    applied = [row for row in writes(chain) if row.get("status") == "APPLIED"]
    cancelled = chain.cycle.future_items.get(out["item_a"])
    moved = chain.cycle.future_items.get(out["item_b"])
    check("1.14 cancel/reschedule are owner writes carried on the committed Decision",
          len(applied) == 2 and cancelled is not None and cancelled.status == "CANCELLED"
          and moved is not None and moved.due_at == iso(at(14)),
          {"applied": applied, "due_b": getattr(moved, "due_at", None)})
    check("1.15 the cancelled item is no longer due",
          [row.item_ref for row in chain.cycle.future_items.due(iso(at(10)))] == [], applied)

    later = window(chain, at(10))
    check("1.16 the next window is quiet and the cancelled item did not revive",
          later.get("status") == "QUIET"
          and chain.cycle.due_future_items(iso(at(10))) == []
          and (chain.cycle.future_items.get(out["item_a"]) or SimpleNamespace()).status
          == "CANCELLED" and len(open_items(chain)) == 1, later)
    out["head"] = head_version(chain, seed)
    out["writer_calls"] = int(chain.writer.call_count)
    return out


# ---------------------------------------------------------------------------------------------
# 阶段 2：真实 WAIT（时间条件 + 释放前台）
# ---------------------------------------------------------------------------------------------


def phase_park(chain) -> dict:
    out: dict = {"phase": "park"}
    seed_activity = chain._seed_activity
    parked = window(chain, at(14), "park", {"in_minutes": 2})
    check("2.1 a real WAIT with a self-chosen time is adopted",
          parked.get("decision") == "WAIT", parked)
    check("2.2 the Activity goes WAITING and the owner releases the foreground",
          activity_status(chain, seed_activity) == "WAITING" and foreground(chain) == "",
          {"status": activity_status(chain, seed_activity), "foreground": foreground(chain)})
    conditions = (chain.runtime.condition_store.load_state().get("conditions") or {})
    mine = [row for row in conditions.values()
            if row.get("activity_id") == seed_activity and row.get("status") == "PENDING"]
    check("2.3 the time condition is the owner's own TIME_AT_OR_AFTER",
          len(mine) == 1 and mine[0].get("condition_kind") == "TIME_AT_OR_AFTER", mine)
    agenda = [row for row in (chain.runtime.waiting_coordinator.list_agenda_items() or [])
              if row.get("activity_id") == seed_activity]
    check("2.4 the owner scheduled exactly one TIME_AT agenda item",
          len(agenda) == 1 and agenda[0].get("trigger_kind") == "TIME_AT", agenda)
    park_items = [row for row in open_items(chain)
                  if row.get("origin") == "PARK_CURRENT_ACTIVITY"]
    out["park_item"] = str((park_items[0] if park_items else {}).get("item_ref") or "")
    check("2.5 the park itself is stored as one of her own later items",
          bool(out["park_item"]) and (park_items[0].get("activity_ref") == seed_activity),
          open_items(chain))
    out["due_at"] = str(mine[0].get("parameters", {}).get("target_time") if mine else "")
    return out


# ---------------------------------------------------------------------------------------------
# 阶段 3：无对象事项 → 既有项目选项 → 真实 owner 创建「别事」
# ---------------------------------------------------------------------------------------------


def phase_option(chain, item_b: str) -> dict:
    out: dict = {"phase": "option"}
    selected = window(chain, at(15), "start_project",
                      {"actions": [{"item_ref": item_b, "op": "cancel",
                                    "reason": "已经做起来了"}]})
    out["option_ref"] = chain.cognition.last_picked_ref
    created = dict(chain.cycle.last_project_created or {})
    out["created_artifact"] = str(created.get("artifact_id") or "")
    check("3.1 with no object the due item is not a FUTURE_ITEM_DUE candidate",
          item_b not in extra_items(chain), sorted(extra_items(chain)))
    check("3.2 it is offered as an existing project option instead",
          item_b in option_bases(chain) and out["option_ref"].startswith("popt:"),
          {"options": option_bases(chain), "picked": out["option_ref"]})
    check("3.3 selecting the option has the real owner create a new work",
          selected.get("outcome") == "ACTED" and bool(out["created_artifact"]), selected)
    check("3.4 the new work is really in the owner, separate from the seed",
          bool(out["created_artifact"])
          and version_count(chain, out["created_artifact"]) >= 1,
          {"versions": version_count(chain, out["created_artifact"] or "")})
    check("3.5 the item itself was not auto-executed (explicitly cancelled in the Decision)",
          (chain.cycle.future_items.get(item_b) or SimpleNamespace()).status == "CANCELLED",
          chain.cycle.future_items.get(item_b))
    out["activity_ref"] = str(selected.get("activity_ref") or "")
    chain._project_activity = out["activity_ref"]
    check("3.6 the other thing really runs as its own ACTIVE Activity",
          activity_status(chain, out["activity_ref"]) == "ACTIVE"
          and foreground(chain) == out["activity_ref"], out["activity_ref"])
    return out


# ---------------------------------------------------------------------------------------------
# 阶段 4：到期资格 → 前台冲突不抢占、不消费 → 释放后显式 RESUME
# ---------------------------------------------------------------------------------------------


def phase_return(chain) -> dict:
    out: dict = {"phase": "return"}
    seed_activity = chain._seed_activity
    conflict = window(chain, at(16), "no_action_propose",
                      {"title": "过一会儿把这件事记一笔", "in_minutes": 2})
    open_elig = eligibilities(chain, seed_activity, "OPEN")
    resumed = [row for row in (chain.cognition.last_candidates or [])
               if row.get("candidate_kind") == "RESUME_ACTIVITY"]
    check("4.1 the owner's due scan opened the resume eligibility itself",
          len(open_elig) == 1, len(open_elig))
    check("4.2 while the other thing holds the foreground there is no RESUME candidate",
          resumed == [], [row.get("candidate_kind") for row in (chain.cognition.last_candidates or [])])
    check("4.3 NO_ACTION does not consume the open eligibility",
          len(eligibilities(chain, seed_activity, "OPEN")) == 1
          and activity_status(chain, chain._project_activity) == "ACTIVE", conflict)

    quiet = window(chain, at(17))
    check("4.4 the same held state stays quiet (no per-window self-excitation)",
          quiet.get("status") == "QUIET" and int(quiet.get("llm_calls") or 0) == 0
          and len(eligibilities(chain, seed_activity, "OPEN")) == 1, quiet)

    set_down = window(chain, at(18), "park", {"in_minutes": 2})
    check("4.5 the other thing is set down too, releasing the foreground",
          set_down.get("decision") == "WAIT" and foreground(chain) == ""
          and activity_status(chain, chain._project_activity) == "WAITING", set_down)
    check("4.6 setting the other thing down did not consume the eligibility",
          len(eligibilities(chain, seed_activity, "OPEN")) == 1, len(eligibilities(chain, seed_activity, "OPEN")))

    back = window(chain, at(19), "resume")
    check("4.7 a new Decision explicitly RESUME the same Activity",
          back.get("decision") == "RESUME" and back.get("outcome") == "ACTED", back)
    check("4.8 it is the same Activity (not a second one) back at ACTIVE",
          activity_status(chain, seed_activity) == "ACTIVE"
          and foreground(chain) == seed_activity, activity_status(chain, seed_activity))
    consumed = eligibilities(chain, seed_activity, "CONSUMED")
    check("4.9 the explicit RESUME consumes that one eligibility exactly once",
          len(consumed) == 1 and eligibilities(chain, seed_activity, "OPEN") == [],
          {"consumed": len(consumed)})
    check("4.10 the other Activity stays WAITING, untouched",
          activity_status(chain, chain._project_activity) == "WAITING",
          activity_status(chain, chain._project_activity))
    out["consumed"] = len(consumed)
    return out


# ---------------------------------------------------------------------------------------------
# 阶段 5：同根重开 —— 事项/状态从磁盘读回，不丢不重放
# ---------------------------------------------------------------------------------------------


def phase_reopen(chain) -> dict:
    out: dict = {"phase": "reopen"}
    state_root = Path(chain.cycle.state_root)
    items_path = state_root / "life_future_items.json"
    before = all_items(chain)
    open_before = {row.item_ref: row.due_at for row in chain.cycle.future_items.open_items()}
    consumed_before = len(eligibilities(chain, chain._seed_activity, "CONSUMED"))
    closure = close_chain(chain)
    check("5.1 closing the runtime releases the canonical lease",
          closure.get("lease_released") is True, closure)
    check("5.2 the items document is on disk in the same root", items_path.exists(),
          str(items_path))

    fresh = open_chain(Path(chain.root), chain.services, chain.clock, chain.cognition)
    out["chain"] = fresh
    after = all_items(fresh)
    check("5.3 reassembling on the same root reads the same items back",
          after == before, {"before": before, "after": after})
    check("5.4 due times and statuses survive the reopen unchanged",
          {row.item_ref: row.due_at for row in fresh.cycle.future_items.open_items()}
          == open_before, open_before)
    check("5.5 the consumed eligibility is still consumed (not replayed)",
          len(eligibilities(fresh, chain._seed_activity, "CONSUMED")) == consumed_before
          and consumed_before == 1, consumed_before)

    writes_before = int(fresh.writer.call_count)
    head_before = head_version(fresh, fresh.data["artifact_id"])
    again = window(fresh, at(20))
    check("5.6 after a reopen nothing is duplicated or replayed",
          len(all_items(fresh)) == len(before)
          and int(fresh.writer.call_count) == writes_before
          and head_version(fresh, fresh.data["artifact_id"]) == head_before, again)
    return out


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def main() -> int:
    root = _fresh_root()
    print("ROOT", root)
    services = None
    chain = None
    fresh = None
    shutdown: dict = {}
    facts: dict = {}
    try:
        prepare_fixture(root)
        clock = Clock(at(0))
        cognition = ScriptedCognition()
        services = _start_services(root / "supply")
        chain = open_chain(root, services, clock, cognition)
        cognition.seed_artifact = str(chain.data["artifact_id"])

        items = phase_items(chain)
        chain._seed_activity = items["activity_ref"]
        park = phase_park(chain)
        option = phase_option(chain, items["item_b"])
        returned = phase_return(chain)
        reopened = phase_reopen(chain)
        fresh = reopened.pop("chain", None)
        facts = {"items": items, "park": park, "option": option, "return": returned,
                 "reopen": reopened, "budget": (fresh.cycle.budget.snapshot()
                                                if fresh is not None else None)}
    except BaseException as exc:  # a real failure is reported as one
        print("SMOKE_ABORTED", type(exc).__name__, str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    finally:
        closer = fresh if fresh is not None else chain
        if closer is not None:
            try:
                close_chain(closer)
            except Exception:  # noqa: BLE001
                pass
        if services is not None:
            try:
                shutdown = _stop_services(services)
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                print("  supply stop error:", type(exc).__name__, str(exc)[:200])

    check("6.1 our own Supply owner is stopped in the production order",
          bool(shutdown) and shutdown.get("drained") in (True, False)
          and str(shutdown.get("socket_handling") or "") != ""
          and not Path(str(shutdown.get("socket_path") or "/nonexistent")).exists(),
          shutdown)
    failed = [row for row in CHECKS if not row[1]]
    print("SUMMARY checks=%d failed=%d" % (len(CHECKS), len(failed)))
    budget = facts.get("budget") or {}
    print("BUDGET", json.dumps({key: budget.get(key) for key in
                                ("spent_today", "cognition_used_today", "actions_used_today",
                                 "remaining_today", "cognition_remaining_today")
                                if key in budget}, ensure_ascii=False, default=str))
    if failed:
        print("SMOKE_FAILED")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
