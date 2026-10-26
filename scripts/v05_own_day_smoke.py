#!/usr/bin/env python3
"""v0.5 零模型隔离冒烟：她自己排今天（正式 Decision → 计划引用 → 预留位 → 真实执行）。

真实件：隔离 Supply owner（真 Unix socket + 落盘）、真实 M0 Memory（EvidenceStore + controls、
正常准入消费者）、B 的 research/practice/private-project gateway、真实 private items 文档、
``AutonomousLifeCycle``、``AutonomousLifeRunner``（正式 check-window）、AG-0 候选、AG-1 原始
JSON 校验、AG-2 采纳、AR-0 受管作品适配器。替身只有 Decision 回答（脚本 raw JSON，明示）与
内容回答（固定 JSON edit）。0 产品模型、0 外网、0 旧实例。

这不是新规则：只验证本轮实现的三件事：
1. 正式 Decision 显式 ``plan_today`` 把既有 fitem 排进今天，持久计划只存引用；
2. operator 正规入口定义绑定真实可续 Artifact 的义务，Life 只读；
3. review/question 占满 2 个常规位后，今日自选/义务各最多 1 个预留位仍进入最终请求，
   选中义务时继续的是它自己绑定的作品；NO_ACTION 不写完成、不写义务定义；
   负控覆盖取消/改期/不可读/源终态/挂起同目标。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_own_day_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
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

import life_day_plan as ldp  # noqa: E402
import life_project_options as lpo  # noqa: E402
import own_day  # noqa: E402

SUBJECT = "test:v05-own-day"
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
DRAIN_TIMEOUT_SECONDS = 10.0

PLAN_TITLE = "把水井那一段补记一笔"
OBLIGATION_TITLE = "上午先把导言读完并写一句"
QUESTION_1 = "第三章为什么每次写到这里就停？"
QUESTION_2 = "村里那口井到底是谁修的？"


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def _fresh_root(tag: str) -> Path:
    base = Path(tempfile.gettempdir()) / "nyairo-v05-own-day"
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = base / ("%s-%s-%s" % (stamp, os.getpid(), tag))
    if root.exists():
        raise SystemExit("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


class Clock:
    """业务时钟替身：预算/权限/事件去重仍读真实时间，只有到期/检查窗/业务日读这里。"""

    def __init__(self, moment: datetime) -> None:
        self.now = moment

    def __call__(self) -> datetime:
        return self.now


class ScriptedCognition:
    """Decision 模型，脚本回答原始 JSON；真实 AG-1 仍做全部校验。"""

    model_id = "stub:v05-own-day"
    model_version = "v05-own-day-1"

    def __init__(self) -> None:
        self.call_count = 0
        self.script: dict = {}
        self.select_source_ref = ""
        self.select_target = ""
        self.last_candidates: list = []
        self.select_missed = ""

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", []) or [])]
        self.last_candidates = candidates
        self.select_missed = ""
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        raw = dict(self.script or {"decision": "NO_ACTION", "selected_candidate_id": None,
                                   "deferred_candidate_ids": [],
                                   "short_rationale": "stub: quiet"})
        raw.setdefault("reason_codes", allowed[:1] or ["NO_LEGAL_CANDIDATE"])
        chosen = None
        if self.select_source_ref:
            chosen = next((row for row in candidates
                           if str(row.get("source_ref") or "") == self.select_source_ref), None)
        if chosen is None and self.select_target:
            wanted = _bare_artifact(self.select_target)

            def _is_work_candidate(row) -> bool:
                refs = [str(ref) for ref in (row.get("target_refs") or [])]
                # The closeout candidate names the same artifact too; it is a different Activity
                # (wrap-up), so pick the continuation candidate explicitly.
                return (not any(ref.startswith("closeout:") for ref in refs)
                        and str(row.get("source_ref") or "").startswith("artifact:"))

            chosen = next((row for row in candidates
                           if _is_work_candidate(row)
                           and any(_bare_artifact(ref) == wanted for ref in
                                   (row.get("target_refs") or []))), None)
        if chosen is None and str(raw.get("decision")) in {"PAUSE", "ABANDON", "CONTINUE"}:
            # PAUSE/ABANDON/CONTINUE are exits from *this* Activity: the candidate that names the
            # running foreground is the one a real answer would select.
            chosen = next((row for row in candidates
                           if "CONTINUE_CURRENT" in str(row.get("candidate_kind") or "")), None)
        if self.select_source_ref and chosen is None:
            self.select_missed = self.select_source_ref
        elif self.select_target and chosen is None:
            self.select_missed = "target:%s" % self.select_target
        if chosen is not None:
            raw["selected_candidate_id"] = str(chosen.get("candidate_id") or "")
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:own-day:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class ScriptedClient:
    """内容模型，脚本回答一条固定 JSON 编辑；无凭证、无网络。"""

    def __init__(self) -> None:
        self.payload = {"kind": "append_paragraph", "text": "第一段正文。",
                        "self_note": "stub: write one paragraph"}
        self.prompts: list = []

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.prompts.append({"system": system_prompt, "user": user_prompt})
        return ModelCallResult(purpose="chat", provider="stub", model="stub",
                               text=json.dumps(self.payload, ensure_ascii=False),
                               latency_ms=1)


def _start_services(root: Path):
    from tests.test_one_day_life import Services

    return Services(Path(root))


def _stop_services(services) -> dict:
    """正规关闭：shutdown → server_close → drain → prepare_socket_path → close。"""

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


def build_chain(tag: str):
    """隔离真 owner 链；调用方负责 close_chain。"""

    return _boot_chain(_fresh_root(tag), fresh=True)


def rebuild_chain(chain):
    """同根新装配：同一个实例/Supply/Memory 数据，关闭旧 runtime 后重新起服务与 cycle。

    只复用磁盘上已存在的 owner 状态，不重新 prepare、不复制文件、不迁移数据。 允许同 PID
    （本夹具就是在同一测试进程里重装配），不冒 cross-PID/crash 恢复。
    """

    return _boot_chain(Path(chain.root), fresh=False)


def _boot_chain(root: Path, *, fresh: bool):
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from components.worldgen import schema
    from components.worldgen.instance import init_instance
    from components.worldgen.tests._setup import make_valid_docs
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort, life_conversation_id
    from memory_control import MemoryControlLedger
    from model_continuation_writer import ModelContinuationWriter
    from tests.test_one_day_life import ENV_GATES

    services = _start_services(root / "supply")
    runtime = None
    try:
        package_dir = root / "package"
        world_home = root / "world-home"
        instance = root / "instance"
        if fresh:
            schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
            init_instance(package_dir, world_home)
            with mock.patch.dict(os.environ, ENV_GATES):
                prepared = odl.prepare(instance=instance, supply_socket=services.supply_path,
                                       governance_socket=services.gov_path, subject=SUBJECT,
                                       title="v0.5 own day",
                                       world_package=package_dir, create_private_items=True)
            if not prepared.get("ok"):
                raise SystemExit("prepare failed: %r" % (prepared,))
        elif not odl.instance_path(instance).exists():
            raise SystemExit("reuse root has no instance: %s" % instance)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        memory_dir = root / "memory"
        memory_dir.mkdir(exist_ok=True)
        evidence_db = memory_dir / "memory.sqlite3"
        controls_db = memory_dir / "controls.sqlite3"
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
            "NYA02_MEMORY_DB": str(evidence_db),
            "NYA02_MEMORY_CONTROLS_DB": str(controls_db),
            "NYA02_MEMORY_SUBJECT": "chiyo",
            "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES": "1",
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        MemoryControlLedger(str(controls_db), str(evidence_db), owner="chiyo",
                            conversation=life_conversation_id("chiyo")).initialize()
        cognition = ScriptedCognition()
        client = ScriptedClient()
        writer = ModelContinuationWriter(client, max_new_chars=300)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        memory_port = LifeExperiencePort(str(evidence_db), controls_db=str(controls_db),
                                         subject="chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=consumer)
        clock = Clock(T0)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True, clock=clock)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        if (assembled.get("practice") or {}).get("status") != "READY":
            raise SystemExit("practice workspace not READY: %r" % (assembled.get("practice"),))
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], runner=assembled["runner"],
                               cognition=cognition, client=client, writer=writer,
                               data=data, env=env, port=port, memory_port=memory_port,
                               evidence_db=evidence_db, controls_db=controls_db,
                               clock=clock, instance=instance)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        try:
            _stop_services(services)
        except Exception:
            pass
        raise


def close_chain(chain) -> dict:
    report = {"runtime_closed": False, "lease_released": False, "services": {}}
    try:
        chain.runtime.close()
        report["runtime_closed"] = True
        report["lease_released"] = not bool(getattr(chain.runtime.lease, "held", True))
    except Exception as exc:  # 打印而不是隐藏
        print("  runtime close error:", type(exc).__name__, str(exc)[:200])
    try:
        report["services"] = _stop_services(chain.services)
    except Exception as exc:  # 打印而不是隐藏
        print("  supply stop error:", type(exc).__name__, str(exc)[:200])
    return report


def mark_unclear(chain, *, marker_id: str, note: str) -> None:
    """用真实 private items owner 写一条她自己的 unclear 标记（正常入口）。"""

    import private_daily_items as pdi

    seed = str(chain.data["artifact_id"])
    point = chain.port.resume_point(seed)
    revision = str(getattr(point, "head_version_id", "") or getattr(point, "version_id", ""))
    store = pdi.PrivateDailyItems(chain.port, str(chain.data["private_items_artifact_id"]),
                                  workspace_id=str(chain.data.get("workspace_id") or ""),
                                  grant_id=str(chain.data["artifact_grant_id"]))
    result = store.mark_unclear(source_id=seed, source_revision=revision or "v1",
                                note=note, marker_id=marker_id,
                                operation_key="op:%s" % marker_id)
    if not getattr(result, "ok", True):
        raise SystemExit("mark_unclear failed: %r" % (result,))


def window(chain, moment: datetime, *, decision: str = "NO_ACTION",
           select_source_ref: str = "", select_target: str = "", later_item=None,
           plan_today=None, future_actions=None) -> tuple[dict, list, bool]:
    chain.clock.now = moment
    cog = chain.cognition
    script = {"decision": decision, "selected_candidate_id": None,
              "deferred_candidate_ids": [], "short_rationale": "stub: own day window"}
    if later_item is not None:
        script["later_item"] = dict(later_item)
    if plan_today is not None:
        script["plan_today"] = dict(plan_today)
    if future_actions is not None:
        script["future_item_actions"] = [dict(row) for row in future_actions]
    cog.script = script
    cog.select_source_ref = str(select_source_ref or "")
    cog.select_target = str(select_target or "")
    before = cog.call_count
    record = chain.runner.run_due(act=True)
    asked = cog.call_count > before
    candidates = list(cog.last_candidates) if asked else []
    missed = str(cog.select_missed or "")
    cog.select_source_ref = ""
    cog.select_target = ""
    cog.script = {}
    cog.select_missed = ""
    if missed:
        record = dict(record)
        record["select_missed"] = missed
    return record.to_dict(), candidates, asked


# ---------------------------------------------------------------------------------------------
# 安全投影：只保留 ID/枚举/计数/标志，绝不回显正文
# ---------------------------------------------------------------------------------------------


def extra(record) -> dict:
    return dict((record or {}).get("extra") or {})


def safe_options(options) -> list[dict]:
    return [{
        "option_ref": str(row.get("option_ref") or ""),
        "kind": str(row.get("kind") or ""),
        "basis_ref": str(row.get("basis_ref") or ""),
        "work_ref": str(row.get("work_ref") or ""),
        "reserved": bool(row.get("reserved")),
    } for row in (options or [])]


def safe_candidates(candidates) -> list[dict]:
    return [{
        "candidate_id": str(row.get("candidate_id") or ""),
        "candidate_kind": str(row.get("candidate_kind") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "target_refs": [str(ref) for ref in (row.get("target_refs") or [])],
        "legal_decisions": [str(code) for code in (row.get("legal_decisions") or [])],
    } for row in (candidates or [])]


def work_ref(value) -> str:
    text = str(value or "")
    return text if text.startswith("artifact:") else ("artifact:%s" % text if text else "")


def _bare_artifact(value) -> str:
    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def item_row(chain, item_ref: str) -> dict:
    row = chain.cycle.future_items.get(item_ref)
    return row.to_dict() if row is not None else {}


def seed_head(chain) -> str:
    point = chain.port.resume_point(str(chain.data["artifact_id"]))
    return str(getattr(point, "head_version_id", "") or "")


def foreground(chain) -> str:
    return str(chain.runtime.canonical_state().get("foreground_activity_ref") or "")


def plan_file(chain) -> Path:
    return chain.cycle.day_plan.path


def obligation_file(chain) -> Path:
    return chain.cycle.day_obligations.path


def file_revision(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("revision") or 0)
    except Exception:
        return -1


def _refusal(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except ldp.DayPlanError as exc:
        return str(exc.reason_code)
    except Exception as exc:  # 其它拒绝也具名保留
        return "%s:%s" % (type(exc).__name__, str(exc)[:80])
    return ""


def _main_scene(tag: str) -> dict:
    chain = build_chain(tag)
    facts: dict = {"refs": {}, "plan": {}, "obligation": {}, "competition": {},
                   "execution": {}, "negative": {}, "quiet": {}, "reassembly": {}}
    saved_obligation: dict = {}
    try:
        seed = work_ref(chain.data["artifact_id"])
        facts["refs"]["seed_artifact"] = seed
        # 两条真实 private unclear 标记：review/question 两条常规位（本轮用两个 question）。
        mark_unclear(chain, marker_id="unclear:own-day:1", note=QUESTION_1)
        mark_unclear(chain, marker_id="unclear:own-day:2", note=QUESTION_2)

        facts["reassembly"]["empty_plan_before"] = (
            chain.cycle.today_plan_view(now=iso(T0)).get("entries"))

        # ① 正式 Decision 先建一条普通 fitem，再显式排入今天。
        first, _c1, asked1 = window(chain, at(0), later_item={"title": PLAN_TITLE,
                                                              "in_minutes": 5})
        item = next((row.to_dict() for row in chain.cycle.future_items.open_items()
                     if str(row.title or "") == PLAN_TITLE), {})
        item_ref = str(item.get("item_ref") or "")
        facts["refs"]["item_ref"] = item_ref
        facts["plan"].update({
            "create_asked": bool(asked1),
            "create_status": str(first.get("status") or ""),
            "create_decision": str(first.get("decision") or ""),
            "item_due_at": str(item.get("due_at") or ""),
        })

        # 到点窗口：A 到期时她显式把它排进今天。计划写只发生在 Decision 提交之后。
        # 同一 Decision 还留一条 3 分钟后的“稍后”（due 8），作为之后一次真实集合变化。
        plan_round, _c2, asked2 = window(
            chain, at(5), plan_today={"item_ref": item_ref},
            later_item={"title": "集合变化用第二条", "in_minutes": 3})
        writes = [dict(row) for row in (chain.cycle.last_day_plan_writes or [])]
        entry = (chain.cycle.day_plan.for_date(ldp.business_date(iso(at(5)))) or [{}])[0]
        facts["refs"]["plan_ref"] = str(entry.get("plan_ref") or "")
        facts["plan"].update({
            "asked": bool(asked2),
            "status": str(plan_round.get("status") or ""),
            "decision": str(plan_round.get("decision") or ""),
            "writes": [{k: row.get(k) for k in
                        ("op", "status", "created", "plan_ref", "item_ref", "date")}
                       for row in writes],
            "entry": {k: entry.get(k) for k in
                      ("plan_ref", "date", "item_ref", "status", "source", "decision_ref")},
            "planned_date": entry.get("date"),
            "business_date": ldp.business_date(iso(at(5))),
            "item_status": str(item_row(chain, item_ref).get("status") or ""),
            "plan_revision": file_revision(plan_file(chain)),
        })
        # 同一 Decision 重放：真实 store 不新建（域层幂等，不靠夹具）。
        same = chain.cycle.day_plan.plan(item_row(chain, item_ref),
                                         date=entry.get("date"),
                                         decision_ref=entry.get("decision_ref"),
                                         now=iso(at(5)))
        facts["plan"]["same_decision_created"] = bool(same.get("created"))

        # ② operator 正规入口：未知目标拒绝；合法义务绑定真实可续 Artifact。
        facts["obligation"]["unknown_target_reason"] = _refusal(
            own_day.import_obligation,
            instance=str(chain.instance), date=ldp.business_date(iso(at(0))),
            title="不该被写进去的义务", target_ref="artifact:does-not-exist",
            due_at=iso(at(5)), source_ref="operator:unknown", operation_key="oblig:unknown",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        imported = own_day.import_obligation(
            instance=str(chain.instance), date=ldp.business_date(iso(at(0))),
            title=OBLIGATION_TITLE, target_ref=seed, due_at=iso(at(5)),
            source_ref="operator:own-day:1", operation_key="oblig:own-day:1",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        obligation = dict(imported.get("entry") or {})
        facts["refs"]["obligation_ref"] = str(obligation.get("obligation_ref") or "")
        facts["refs"]["obligation_target"] = str(obligation.get("target_ref") or "")
        again = own_day.import_obligation(
            instance=str(chain.instance), date=ldp.business_date(iso(at(0))),
            title=OBLIGATION_TITLE, target_ref=seed, due_at=iso(at(5)),
            source_ref="operator:own-day:1", operation_key="oblig:own-day:1",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        facts["obligation"].update({
            "import_created": bool(imported.get("created")),
            "import_again_created": bool(again.get("created")),
            "definition": {k: obligation.get(k) for k in
                           ("date", "status", "source", "target_ref", "due_at")},
            "reader_class": type(chain.cycle.day_obligations).__name__,
            "reader_has_writer": hasattr(chain.cycle.day_obligations, "import_obligation"),
            "file_revision": file_revision(obligation_file(chain)),
        })
        saved_obligation = obligation_file(chain).read_bytes()

        # 另建一件真实作品：去重只能作用于同一真实目标，不同目标的可执行候选必须仍在。
        other = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品（夹具）",
            content="另一件。", grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="op:own-day:other:1")
        other_ref = "artifact:%s" % _bare_artifact(other.artifact_id)
        facts["refs"]["other_artifact"] = other_ref

        # ③ review/question 占 2 个常规位 + 两条预留都在，合计 4，且最终请求含两者。
        writer_before = int(chain.writer.call_count)
        plan_rev_before = file_revision(plan_file(chain))
        obl_rev_before = file_revision(obligation_file(chain))
        comp, candidates, comp_asked = window(chain, at(6))
        options = safe_options(chain.cycle.last_project_options)
        cands = safe_candidates(candidates)
        option_map = {
            str(row.get("option_ref") or ""): {
                "kind": str(row.get("kind") or ""),
                "work_ref": str(row.get("work_ref") or ""),
                "reserved": bool(row.get("reserved")),
            } for row in (chain.cycle.last_project_options or [])}
        #: 一个候选的“真实可执行目标”：reserved popt 看它自己绑定的 work_ref，artifact
        #: continuation 看 source_ref。  closeout 是另一个合法动作，单独计数，不参与去重。
        executable_entries: list[dict] = []
        closeout_entries: list[dict] = []
        for row in cands:
            refs = list(row.get("target_refs") or [])
            if any(str(ref).startswith("closeout:") for ref in refs):
                closeout_entries.append({"source_ref": row["source_ref"],
                                         "target": _bare_artifact(row["source_ref"])})
                continue
            source_ref = str(row.get("source_ref") or "")
            target = ""
            kind = ""
            if source_ref.startswith(lpo.OPTION_REF_NAMESPACE):
                mapped = option_map.get(source_ref) or {}
                target = _bare_artifact(mapped.get("work_ref"))
                kind = str(mapped.get("kind") or "")
            elif source_ref.startswith("artifact:"):
                target = _bare_artifact(source_ref)
                kind = "ARTIFACT_CONTINUATION"
            if target:
                executable_entries.append({"source_ref": source_ref, "kind": kind,
                                           "target": target})
        seed_bare = _bare_artifact(facts["refs"]["seed_artifact"])
        other_bare = _bare_artifact(other_ref)
        facts["competition"].update({
            "executable_entries": executable_entries,
            "closeout_entries": closeout_entries,
            "seed_entries": [row for row in executable_entries
                             if row["target"] == seed_bare],
            "other_entries": [row for row in executable_entries
                              if row["target"] == other_bare],
            "seed_closeout_present": any(row["target"] == seed_bare
                                         for row in closeout_entries),
        })
        reserved = [row for row in options if row["reserved"]]
        regular = [row for row in options if not row["reserved"]]
        kinds = [row["kind"] for row in reserved]
        by_kind = {row["kind"]: row["option_ref"] for row in reserved}
        facts["refs"]["reserved_self"] = by_kind.get(ldp.KIND_SELF_PLANNED, "")
        facts["refs"]["reserved_obligation"] = by_kind.get(ldp.KIND_OBLIGATION, "")
        #: 结果重读用的关联公式必须与真实候选 id 一致；这里用正式候选回答对照，不手塞。
        from artifact_continuation_source import ArtifactContinuationSource
        obligation_option = by_kind.get(ldp.KIND_OBLIGATION, "")
        facts["competition"]["obligation_candidate_id"] = next(
            (row["candidate_id"] for row in cands
             if row["source_ref"] == obligation_option), "")
        facts["competition"]["obligation_candidate_id_expected"] = (
            ArtifactContinuationSource.extra_candidate_id(obligation_option, 1)
            if obligation_option else "")
        facts["competition"].update({
            "asked": bool(comp_asked),
            "status": str(comp.get("status") or ""),
            "decision": str(comp.get("decision") or ""),
            "options": options,
            "regular_count": len(regular),
            "reserved_kinds": kinds,
            "total": len(options),
            "candidates": cands,
            "reserved_in_request": {
                kind: any(row["source_ref"] == by_kind.get(kind)
                          and "START" in (row.get("legal_decisions") or [])
                          for row in cands)
                for kind in (ldp.KIND_SELF_PLANNED, ldp.KIND_OBLIGATION)},
            "plan_revision_same": file_revision(plan_file(chain)) == plan_rev_before,
            "obligation_revision_same": file_revision(obligation_file(chain)) == obl_rev_before,
            "writer_calls_delta": int(chain.writer.call_count) - writer_before,
            "today_plan": [{k: row.get(k) for k in ("status", "due")}
                           for row in chain.cycle.today_plan_view(
                               now=iso(at(6))).get("entries") or []],
            "today_obligations": [{k: row.get(k) for k in ("result", "due")}
                                  for row in chain.cycle.obligations_view(
                                      now=iso(at(6))).get("entries") or []],
        })
        # 没有 plan_today 字段的回合不写计划；计划文件的 revision 不变。
        facts["plan"]["no_field_writes"] = [
            dict(row) for row in (chain.cycle.last_day_plan_writes or [])]
        facts["plan"]["no_field_plan_revision_same"] = (
            file_revision(plan_file(chain)) == facts["plan"]["plan_revision"])

        # 同状态再考虑：窗口再次到期、集合不变，不花请求（QUIET）。
        quiet_round, quiet_candidates, quiet_asked = window(chain, at(7))
        facts["quiet"] = {
            "asked": bool(quiet_asked),
            "status": str(quiet_round.get("status") or ""),
            "decision": str(quiet_round.get("decision") or ""),
            "candidate_count": len(quiet_candidates),
        }

        # ④ 选中义务预留位：真实继续它绑定的作品；结果投影绑定本次 Decision/回执。
        version_before = seed_head(chain)
        exec_round, exec_candidates, exec_asked = window(
            chain, at(8), decision="START",
            select_source_ref=facts["refs"]["reserved_obligation"],
            # 同一个 Decision 还留一条 3 分钟后的“稍后”：它在 11 分钟到期，作为之后改期回合
            # 的真实集合变化（不是手工改状态）。
            later_item={"title": "集合变化用第四条", "in_minutes": 3})
        receipt = (chain.cycle.last_receipt.to_dict()
                   if chain.cycle.last_receipt is not None else {})
        obligations_after = chain.cycle.obligations_view(
            now=iso(at(8))).get("entries") or []
        obligation_row = next((row for row in obligations_after
                               if row.get("obligation_ref")
                               == facts["refs"]["obligation_ref"]), {})
        facts["execution"].update({
            "asked": bool(exec_asked),
            "status": str(exec_round.get("status") or ""),
            "decision": str(exec_round.get("decision") or ""),
            "outcome": str(exec_round.get("outcome") or ""),
            "selected": str(extra(exec_round).get("selected_candidate_ref") or ""),
            "select_missed": str(exec_round.get("select_missed") or ""),
            "candidate_present": any(
                row["source_ref"] == facts["refs"]["reserved_obligation"]
                for row in safe_candidates(exec_candidates)),
            "version_before": version_before,
            "version_after": seed_head(chain),
            "receipt_artifact": str(receipt.get("artifact_id") or ""),
            "receipt_effect": str(receipt.get("effect_state") or ""),
            "receipt_decision_id": receipt.get("decision_id"),
            "obligation_result": str(obligation_row.get("result") or ""),
            "obligation_evidence_decision": (obligation_row.get("evidence") or {}).get(
                "decision_id"),
            "obligation_file_unchanged": (
                obligation_file(chain).read_bytes() == saved_obligation),
        })

        # 收尾前台后，重复回合不得再写版本/义务文档。
        end_round, _e1, _ea = window(chain, at(9), decision="ABANDON")
        head_after = seed_head(chain)
        repeat, _e2, _eb = window(chain, at(10))
        facts["execution"].update({
            "end_status": str(end_round.get("status") or ""),
            "repeat_status": str(repeat.get("status") or ""),
            "repeat_head_same": seed_head(chain) == head_after,
            "repeat_obligation_file_same": (
                obligation_file(chain).read_bytes() == saved_obligation),
        })

        # ⑤ 改期出今天 → 不再占位；随后取消 → 仍是撤回，不报完成。
        resched, _n1, _na = window(
            chain, at(11),
            future_actions=[{"item_ref": item_ref, "op": "reschedule",
                             "in_minutes": 600}],
            # 改期时再留一条 2 分钟后的“稍后”（due 13），给取消回合一次真实集合变化。
            later_item={"title": "集合变化用第五条", "in_minutes": 2})
        resched_writes = [dict(row) for row in (chain.cycle.last_future_item_writes or [])]
        after_resched, _n2, _na2 = window(chain, at(12))
        resched_kinds = [row["kind"] for row in safe_options(chain.cycle.last_project_options)]
        cancel, _n3, _na3 = window(chain, at(13), future_actions=[
            {"item_ref": item_ref, "op": "cancel"}])
        cancel_writes = [dict(row) for row in (chain.cycle.last_future_item_writes or [])]
        after_cancel, _n4, _na4 = window(chain, at(14))
        facts["negative"]["reschedule"] = {
            "status": str(resched.get("status") or ""),
            "writes": [{k: row.get(k) for k in ("op", "status", "reason_code")}
                       for row in resched_writes],
            "next_status": str(after_resched.get("status") or ""),
            "reserved_present": ldp.KIND_SELF_PLANNED in resched_kinds,
        }
        facts["negative"]["cancel"] = {
            "status": str(cancel.get("status") or ""),
            "writes": [{k: row.get(k) for k in ("op", "status", "reason_code")}
                       for row in cancel_writes],
            "item_status_after": str(item_row(chain, item_ref).get("status") or ""),
            "next_status": str(after_cancel.get("status") or ""),
            "plan_entries": [{k: row.get(k) for k in ("plan_ref", "status", "reason_code")}
                             for row in chain.cycle.today_plan_view(
                                 now=iso(at(14))).get("entries") or []],
        }

        # 不可读义务文件：具名错误，不当空表；恢复后仍可读。
        broken = obligation_file(chain)
        broken.write_text("not json", encoding="utf-8")
        unread, _u1, _ua = window(chain, at(15))
        facts["negative"]["unreadable"] = {
            "status": str(unread.get("status") or ""),
            "day_plan_error": str(chain.cycle.last_day_plan_error or ""),
            "obligation_view_status": str(
                chain.cycle.obligations_view(now=iso(at(15))).get("status") or ""),
        }
        broken.write_bytes(saved_obligation)

        # 源终态：另建一件真实作品，绑定义务后归档 → 预留位撤回（具名）。
        created = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="临时草稿（夹具）",
            content="第一版。", grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="op:own-day:temp:1")
        temp_ref = "artifact:%s" % _bare_artifact(created.artifact_id)
        own_day.import_obligation(
            instance=str(chain.instance), date=ldp.business_date(iso(at(0))),
            title="归档后不该再出现", target_ref=temp_ref, due_at=iso(at(16)),
            source_ref="operator:own-day:temp", operation_key="oblig:own-day:temp",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        point = chain.port.read_artifact(temp_ref)["artifact"]
        chain.port.archive_artifact(
            artifact_id=temp_ref, expected_revision=int(point.get("revision") or 1),
            grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="op:own-day:temp:archive")
        terminal, _t1, _ta = window(chain, at(16))
        terminal_withheld = [row for row in chain.cycle._round_obligation_withheld
                             if row.get("kind") == ldp.KIND_OBLIGATION]
        terminal_options = safe_options(chain.cycle.last_project_options)
        facts["negative"]["terminal_target"] = {
            "status": str(terminal.get("status") or ""),
            "withheld_reasons": [str(row.get("reason_code")) for row in terminal_withheld],
            "terminal_option_offered": any(
                row.get("work_ref") == temp_ref for row in terminal_options),
        }

        # 同根重装配（存储层）：新实例读同一文件，内容一致且零写。
        plan_before = plan_file(chain).read_bytes()
        plan_after_store = ldp.DayPlan(chain.cycle.state_root)
        reread = plan_after_store.for_date(ldp.business_date(iso(at(0))))
        facts["reassembly"].update({
            "plan_count": len(reread),
            "plan_item_ref": str((reread[0] or {}).get("item_ref") if reread else ""),
            "file_unchanged": plan_file(chain).read_bytes() == plan_before,
        })
        return facts
    finally:
        _last_close.clear()
        _last_close.update(close_chain(chain))


def _paused_scene(tag: str) -> dict:
    """挂起同目标：义务预留位撤回，且不得额外 START 同一对象。"""

    chain = build_chain(tag)
    facts: dict = {"refs": {}, "paused": {}}
    try:
        seed = work_ref(chain.data["artifact_id"])
        facts["refs"]["paused_seed_artifact"] = seed
        own_day.import_obligation(
            instance=str(chain.instance), date=ldp.business_date(iso(at(0))),
            title="挂起后不该再开第二件", target_ref=seed, due_at=iso(at(6)),
            source_ref="operator:own-day:paused", operation_key="oblig:own-day:paused",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        started, _c1, asked1 = window(chain, at(0), decision="START", select_target=seed)
        paused, _c2, asked2 = window(chain, at(1), decision="PAUSE")
        writer_before = int(chain.writer.call_count)
        version_before = seed_head(chain)
        due_round, due_candidates, due_asked = window(chain, at(6))
        withheld = [row for row in chain.cycle._round_obligation_withheld
                    if row.get("kind") == ldp.KIND_OBLIGATION]
        cands = safe_candidates(due_candidates)
        facts["paused"].update({
            "start_asked": bool(asked1),
            "start_status": str(started.get("status") or ""),
            "start_missed": str(started.get("select_missed") or ""),
            "pause_asked": bool(asked2),
            "pause_status": str(paused.get("status") or ""),
            "pause_outcome": str(paused.get("outcome") or ""),
            "pause_decision": str(paused.get("decision") or ""),
            "pause_detail": str(paused.get("detail") or "")[:200],
            "foreground_after_pause": foreground(chain),
            "due_asked": bool(due_asked),
            "due_status": str(due_round.get("status") or ""),
            "due_decision": str(due_round.get("decision") or ""),
            "withheld_reasons": [str(row.get("reason_code")) for row in withheld],
            "same_target_candidate": any(
                seed in (row.get("target_refs") or []) for row in cands),
            "writer_calls_delta": int(chain.writer.call_count) - writer_before,
            "head_same": seed_head(chain) == version_before,
            "open_activity_statuses": [str(row.get("status") or "")
                                       for row in chain.cycle.open_activities()],
        })
        return facts
    finally:
        _last_close.clear()
        _last_close.update(close_chain(chain))


def _ledger_actions(chain, target_ref: str) -> list[dict]:
    """Only IDs/enums from the AR-0 ledger for actions on one real target."""

    reader = getattr(chain.runtime, "action_read", None)
    if reader is None:
        return []
    bare = _bare_artifact(target_ref)
    rows: list[dict] = []
    try:
        actions = list(reader.list_actions() or [])
    except Exception:
        return []
    for action in actions:
        if _bare_artifact(action.get("target_ref")) != bare:
            continue
        receipts = []
        try:
            receipts = list(reader.list_receipts(str(action.get("action_id"))) or [])
        except Exception:
            receipts = []
        rows.append({
            "action_id": str(action.get("action_id") or ""),
            "status": str(action.get("status") or ""),
            "decision_ref": str(action.get("decision_ref") or ""),
            "activity_ref": str(action.get("activity_ref") or ""),
            "receipt_count": len(receipts),
        })
    return rows


def _reassembly_scene(tag: str) -> dict:
    """义务真实成功后关闭 runtime/服务，再同根新装配读取持久结果；不重放旧 Decision。"""

    chain = build_chain(tag)
    rebuilt = None
    facts: dict = {"refs": {}, "execution": {}, "after": {}}
    try:
        seed = work_ref(chain.data["artifact_id"])
        day = ldp.business_date(iso(at(0)))
        imported = own_day.import_obligation(
            instance=str(chain.instance), date=day,
            title="重装配后仍要记得读完", target_ref=seed, due_at=iso(at(5)),
            source_ref="operator:own-day:reassembly", operation_key="oblig:reassembly:1",
            target_verifier=chain.port.resume_point, now=iso(at(0)))
        obligation = dict(imported.get("entry") or {})
        obligation_ref = str(obligation.get("obligation_ref") or "")
        option_ref = ldp.obligation_option_ref(obligation_ref, seed, day)
        facts["refs"] = {"seed_artifact": seed, "obligation_ref": obligation_ref,
                         "obligation_option": option_ref}

        round1, candidates1, asked1 = window(
            chain, at(6), decision="START", select_source_ref=option_ref)
        receipt = (chain.cycle.last_receipt.to_dict()
                   if chain.cycle.last_receipt is not None else {})
        view_before = chain.cycle.obligations_view(now=iso(at(6)))
        row_before = next((row for row in view_before.get("entries") or []
                           if row.get("obligation_ref") == obligation_ref), {})
        actions_before = _ledger_actions(chain, seed)
        fulfilling = next((row for row in actions_before
                           if row["decision_ref"] == str(receipt.get("decision_id") or "")), {})
        from artifact_continuation_source import ArtifactContinuationSource
        offered_ids = [row.get("candidate_id") for row in candidates1
                       if row.get("source_ref") == option_ref]
        facts["execution"] = {
            "asked": bool(asked1),
            "outcome": str(round1.get("outcome") or ""),
            "select_missed": str(round1.get("select_missed") or ""),
            "candidate_ids": offered_ids,
            "candidate_id_expected": ArtifactContinuationSource.extra_candidate_id(
                option_ref, 1),
            "result": str(row_before.get("result") or ""),
            "decision_id": str(receipt.get("decision_id") or ""),
            "version_id": str(receipt.get("version_id") or ""),
            "action_id": str((fulfilling or {}).get("action_id") or ""),
            "action_status": str((fulfilling or {}).get("status") or ""),
            "activity_ref": str((fulfilling or {}).get("activity_ref") or ""),
            "obligation_file": obligation_file(chain).read_bytes(),
        }
        head_before = seed_head(chain)
        facts["execution"]["head"] = head_before
        facts["execution"]["action_count"] = len(actions_before)

        # 关闭原 runtime 并排空服务；随后同根重装配（同一实例/Supply/Memory 数据）。
        closing = close_chain(chain)
        facts["execution"]["closed"] = closing
        rebuilt = rebuild_chain(chain)
        chain = None

        # 只读结果必须先于任何候选/执行回合出现。
        view_after = rebuilt.cycle.obligations_view(now=iso(at(20)))
        row_after = next((row for row in view_after.get("entries") or []
                          if row.get("obligation_ref") == obligation_ref), {})
        evidence = dict(row_after.get("evidence") or {})
        actions_after = _ledger_actions(rebuilt, seed)
        head_after_view = seed_head(rebuilt)
        bytes_after_view = obligation_file(rebuilt).read_bytes()
        options = rebuilt.cycle.project_options(now=iso(at(20)))
        reserved = [dict(row) for row in (options or []) if row.get("reserved")]
        window2, candidates2, asked2 = window(rebuilt, at(20))
        head_final = seed_head(rebuilt)
        actions_final = _ledger_actions(rebuilt, seed)
        bytes_final = obligation_file(rebuilt).read_bytes()
        facts["after"] = {
            "result": str(row_after.get("result") or ""),
            "evidence_decision_id": str(evidence.get("decision_id") or ""),
            "evidence_action_id": str(evidence.get("action_id") or ""),
            "evidence_activity_ref": str(evidence.get("activity_ref") or ""),
            "evidence_version_id": str(evidence.get("version_id") or ""),
            "evidence_path": str(evidence.get("evidence_path") or ""),
            "operator_bytes_unchanged": (
                bytes_after_view == facts["execution"]["obligation_file"]
                and bytes_final == facts["execution"]["obligation_file"]),
            "head_same": head_after_view == head_before and head_final == head_before,
            "action_count_same": (len(actions_final) == facts["execution"]["action_count"]),
            "obligation_reserved_present": any(
                row.get("kind") == ldp.KIND_OBLIGATION
                and str(row.get("basis_ref") or "") == obligation_ref
                for row in reserved),
            "obligation_candidate_present": any(
                str(row.get("source_ref") or "") == option_ref
                for row in candidates2),
            "window_asked": bool(asked2),
            "window_status": str(window2.get("status") or ""),
            "window_decision": str(window2.get("decision") or ""),
        }
        return facts
    finally:
        if rebuilt is not None:
            _last_close.clear()
            _last_close.update(close_chain(rebuilt))
        elif chain is not None:
            _last_close.clear()
            _last_close.update(close_chain(chain))


def run_own_day(tag: str = "run") -> dict:
    main = _main_scene("%s-main" % tag)
    paused = _paused_scene("%s-paused" % tag)
    reassembly = _reassembly_scene("%s-reassembly" % tag)
    facts = dict(main)
    #: 三个场景各有自己的隔离根；只有 main 的 seed_artifact/item_ref 是原有断言引用的身份。
    #: 后两个场景的同名键不能覆盖它。
    facts["refs"] = {**paused.get("refs", {}), **reassembly.get("refs", {}),
                     **main.get("refs", {})}
    facts["paused"] = paused.get("paused", {})
    facts["reassembly_obligation"] = reassembly.get("execution", {})
    facts["reassembly_after"] = reassembly.get("after", {})
    return facts


_last_close: dict = {}


def last_close() -> dict:
    return dict(_last_close)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (real Supply socket); run in WSL/Linux")
        return 0
    try:
        facts = run_own_day("smoke")
    except BaseException as exc:  # 真失败如实报告
        print("SMOKE_ABORTED", type(exc).__name__, str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    closure = last_close()
    print("SAFE_FACTS", json.dumps(facts, ensure_ascii=False, default=str)[:12000])
    print("CLOSE", json.dumps(closure, ensure_ascii=False, default=str))

    plan = facts.get("plan", {})
    obligation = facts.get("obligation", {})
    comp = facts.get("competition", {})
    execution = facts.get("execution", {})
    negative = facts.get("negative", {})
    reassembly = facts.get("reassembly", {})
    quiet = facts.get("quiet", {})
    paused = facts.get("paused", {})
    reassembly_after = facts.get("reassembly_after", {})

    checks = [
        ("plan: formal Decision writes one reference for today",
         plan.get("create_decision") == "NO_ACTION"
         and plan.get("status") == "NO_ACTION"
         and plan.get("planned_date") == plan.get("business_date")
         and plan.get("entry", {}).get("item_ref") == facts["refs"].get("item_ref")
         and plan.get("entry", {}).get("source") == "SUBJECT_DECISION"
         and [row.get("status") for row in plan.get("writes") or []] == ["APPLIED"]
         and plan.get("item_status") == "OPEN"
         and plan.get("same_decision_created") is False),
        ("plan: no-field round writes nothing",
         plan.get("no_field_writes") == []
         and plan.get("no_field_plan_revision_same") is True),
        ("obligation: operator entry refuses unknown target, writes once, Life is read-only",
         obligation.get("unknown_target_reason") in
         {"OBLIGATION_TARGET_UNKNOWN", "OBLIGATION_TARGET_UNREADABLE"}
         and obligation.get("import_created") is True
         and obligation.get("import_again_created") is False
         and obligation.get("definition", {}).get("source") == "OPERATOR"
         and obligation.get("definition", {}).get("target_ref") ==
         facts["refs"].get("seed_artifact")
         and obligation.get("reader_class") == "DayObligations"
         and obligation.get("reader_has_writer") is False),
        ("competition: 2 regular + 2 reserved, both in the final request with START legal",
         comp.get("asked") is True
         and comp.get("regular_count") == lpo.MAX_OPTIONS
         and comp.get("total") == lpo.MAX_OPTIONS + 2
         and comp.get("reserved_kinds") == [ldp.KIND_SELF_PLANNED, ldp.KIND_OBLIGATION]
         and comp.get("reserved_in_request", {}).get(ldp.KIND_SELF_PLANNED) is True
         and comp.get("reserved_in_request", {}).get(ldp.KIND_OBLIGATION) is True
         and comp.get("writer_calls_delta") == 0
         and comp.get("plan_revision_same") is True
         and comp.get("obligation_revision_same") is True),
        ("competition: one executable entry per real target, obligation label kept, closeout kept",
         len(comp.get("seed_entries") or []) == 1
         and (comp.get("seed_entries") or [{}])[0].get("source_ref")
         == facts["refs"].get("reserved_obligation")
         and (comp.get("seed_entries") or [{}])[0].get("kind") == ldp.KIND_OBLIGATION
         and comp.get("seed_closeout_present") is True
         and len(comp.get("other_entries") or []) == 1
         and comp.get("obligation_candidate_id")
         == comp.get("obligation_candidate_id_expected")),
        ("quiet: the same set spends no request",
         quiet.get("asked") is False and quiet.get("status") == "QUIET"),
        ("execution: the obligation continues its own work and binds this Decision/receipt",
         execution.get("asked") is True
         and execution.get("outcome") == "ACTED"
         and execution.get("select_missed") == ""
         and execution.get("version_before") != execution.get("version_after")
         and execution.get("obligation_result") == "DONE"
         and execution.get("obligation_evidence_decision")
         == execution.get("receipt_decision_id")
         and execution.get("receipt_decision_id")
         and execution.get("obligation_file_unchanged") is True),
        ("execution: repeat round does not replay the step",
         execution.get("repeat_status") in {"NO_ACTION", "QUIET"}
         and execution.get("repeat_head_same") is True
         and execution.get("repeat_obligation_file_same") is True),
        ("negative: reschedule/cancel withdraw the reserved slot",
         any(row.get("op") == "reschedule" and row.get("status") == "APPLIED"
             for row in negative.get("reschedule", {}).get("writes") or [])
         and negative.get("reschedule", {}).get("reserved_present") is False
         and any(row.get("op") == "cancel" and row.get("status") == "APPLIED"
                 for row in negative.get("cancel", {}).get("writes") or [])
         and negative.get("cancel", {}).get("item_status_after") == "CANCELLED"
         and all(row.get("status") == "ABANDONED"
                 for row in negative.get("cancel", {}).get("plan_entries") or [])),
        ("negative: unreadable obligations are named, not an empty table",
         negative.get("unreadable", {}).get("obligation_view_status") == "READ_FAILED"
         and "OBLIGATIONS_STATE" in str(
             negative.get("unreadable", {}).get("day_plan_error") or "")),
        ("negative: an archived target is withheld by name",
         "OBLIGATION_TARGET_NOT_EXECUTABLE" in
         (negative.get("terminal_target", {}).get("withheld_reasons") or [])
         and negative.get("terminal_target", {}).get("terminal_option_offered") is False),
        ("reassembly: the same state root reads the plan back without writing",
         reassembly.get("plan_count", 0) >= 1
         and reassembly.get("plan_item_ref") == facts["refs"].get("item_ref")
         and reassembly.get("file_unchanged") is True),
        ("reassembly: the successful obligation is DONE from persisted Action/Decision/receipt",
         facts.get("reassembly_obligation", {}).get("result") == ldp.OBLIGATION_RESULT_DONE
         and facts.get("reassembly_obligation", {}).get("action_status") == "SUCCEEDED"
         and facts.get("reassembly_obligation", {}).get("candidate_ids")
         == [facts.get("reassembly_obligation", {}).get("candidate_id_expected")]
         and reassembly_after.get("result") == ldp.OBLIGATION_RESULT_DONE
         and reassembly_after.get("evidence_path") == ldp.EVIDENCE_PATH_PERSISTED
         and reassembly_after.get("evidence_decision_id")
         == facts.get("reassembly_obligation", {}).get("decision_id")
         and reassembly_after.get("evidence_action_id") ==
         facts.get("reassembly_obligation", {}).get("action_id")
         and reassembly_after.get("evidence_activity_ref") ==
         facts.get("reassembly_obligation", {}).get("activity_ref")
         and reassembly_after.get("evidence_version_id") ==
         facts.get("reassembly_obligation", {}).get("version_id")),
        ("reassembly: no replay, no duplicate executable obligation, zero write",
         reassembly_after.get("head_same") is True
         and reassembly_after.get("action_count_same") is True
         and reassembly_after.get("operator_bytes_unchanged") is True
         and reassembly_after.get("obligation_reserved_present") is False
         and reassembly_after.get("obligation_candidate_present") is False),
        ("paused: the same target is withheld and no second Activity starts",
         paused.get("pause_outcome") == "PAUSED"
         and paused.get("due_asked") is True
         and "PAUSED_TARGET_REQUIRES_RESUME" in (paused.get("withheld_reasons") or [])
         and paused.get("same_target_candidate") is False
         and paused.get("writer_calls_delta") == 0
         and paused.get("head_same") is True
         and "ACTIVE" not in (paused.get("open_activity_statuses") or [])),
    ]
    failed = 0
    for name, ok in checks:
        print("CHECK %-78s %s" % (name, "PASS" if ok else "FAIL"))
        failed += 0 if ok else 1
    print("SUMMARY checks=%d failed=%d" % (len(checks), failed))
    if failed:
        print("SMOKE_FAILED")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
