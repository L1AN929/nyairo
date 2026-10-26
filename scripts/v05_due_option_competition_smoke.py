#!/usr/bin/env python3
"""v0.5 零模型隔离冒烟：无对象到期事项与 review/question 的名额竞争。

只回答一个事实问题，不改业务规则：

* 当 ``life_project_options.build_options`` 同时有 review 与 question 两个来源、而
  ``MAX_OPTIONS = 2`` 时，一个**没有对象**的到期事项还会不会出现在最终 Decision 请求里？
* 反过来，只有这一条到期事项时，它对应的 ``popt:`` 候选是否在正式请求里且可 START？
* 带作品对象的到期事项，其独立的 ``fitem:`` 候选是否不受这两个名额影响？

真实件：隔离 Supply owner（真 Unix socket + 落盘）、真实 M0 Memory（EvidenceStore +
controls、正常准入消费者）、B 的 practice/private-project gateway、真实 private items
文档、``AutonomousLifeCycle``、``AutonomousLifeRunner``（正式 check-window）、AG-0 候选、
AG-1 原始 JSON 校验、AG-2 采纳、AR-0 受管作品适配器。替身只有 Decision 回答（脚本 raw
JSON，明示）与内容回答（固定 JSON edit）。0 产品模型、0 外网、0 旧实例。

本脚本只读业务代码，不修改任何生产文件。夹具根保留以便复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/v05_due_option_competition_smoke.py
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

import life_project_options as lpo  # noqa: E402

SUBJECT = "test:v05-due-option-competition"
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
#: 与 build_options 当前默认一致；断言只在“当前布置”下成立。
LIMIT = 2
DRAIN_TIMEOUT_SECONDS = 10.0

DUE_A_TITLE = "把水井那一段补记一笔"
DUE_B_TITLE = "把导言最后一句改顺"
QUESTION_1_NOTE = "第三章为什么每次写到这里就停？"
QUESTION_2_NOTE = "村里那口井到底是谁修的？"


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def _fresh_root(tag: str) -> Path:
    base = Path(tempfile.gettempdir()) / "nyairo-v05-due-option"
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = base / ("%s-%s-%s" % (stamp, os.getpid(), tag))
    if root.exists():
        raise SystemExit("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


class Clock:
    """业务时钟替身：预算/权限/事件去重仍读真实时间，只有到期与检查窗读这里。"""

    def __init__(self, moment: datetime) -> None:
        self.now = moment

    def __call__(self) -> datetime:
        return self.now


class ScriptedCognition:
    """Decision 模型，脚本回答原始 JSON；真实 AG-1 仍做全部校验。"""

    model_id = "stub:v05-due-option-competition"
    model_version = "v05-due-option-competition-1"

    def __init__(self) -> None:
        self.call_count = 0
        self.script: dict = {}
        self.select_source_ref = ""
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
        wanted = str(self.select_source_ref or "")
        if wanted:
            chosen = next((row for row in candidates
                           if str(row.get("source_ref") or "") == wanted), None)
            if chosen is None:
                self.select_missed = wanted
            else:
                raw["selected_candidate_id"] = str(chosen.get("candidate_id") or "")
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:due-option:%d" % self.call_count,
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

    root = _fresh_root(tag)
    services = _start_services(root / "supply")
    runtime = None
    try:
        package_dir = root / "package"
        schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
        world_home = root / "world-home"
        init_instance(package_dir, world_home)
        instance = root / "instance"
        with mock.patch.dict(os.environ, ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=services.supply_path,
                                   governance_socket=services.gov_path, subject=SUBJECT,
                                   title="v0.5 due option competition",
                                   world_package=package_dir, create_private_items=True)
        if not prepared.get("ok"):
            raise SystemExit("prepare failed: %r" % (prepared,))
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        memory_dir = root / "memory"
        memory_dir.mkdir()
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
            # 逻辑分钟的检查窗：每个窗口由本脚本注入的业务时钟推进。
            "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES": "1",
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        # M0 的正常控制初始化（与既有 result-method 夹具同一条路径）。
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
        if not cognition or not assembled.get("runner"):
            raise SystemExit("runner missing from assembly")
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], runner=assembled["runner"],
                               cognition=cognition, client=client, writer=writer,
                               data=data, env=env, port=port, memory_port=memory_port,
                               evidence_db=evidence_db, controls_db=controls_db,
                               clock=clock)
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


def import_research_material(chain) -> dict:
    """操作者导入的公开测试文本，作为 question 研究选项的真实材料（正常入口）。"""

    import research_workspace_service as rws

    service = rws.research_from_env(chain.port, dict(chain.env))
    if service is None:
        raise SystemExit("research workspace did not resolve from env")
    answer = service.import_material(
        title="田野笔记（测试夹具）", text="第一章：村里的水井在旱季仍然有水。",
        source_kind="user_supplied", origin="操作者导入的测试文本",
        operation_key="op:material:due-option-competition:1")
    if not answer.get("ok"):
        raise SystemExit("material import failed: %r" % (answer,))
    material = answer["data"]["material"]
    return {"artifact_id": str(material["artifact_id"]),
            "source_revision": str(material["source_revision"])}


def mark_unclear(chain, *, marker_id: str, note: str) -> str:
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
    return marker_id


def window(chain, moment: datetime, *, decision: str = "NO_ACTION",
           select_source_ref: str = "", later_item=None) -> tuple[dict, list, bool]:
    chain.clock.now = moment
    cog = chain.cognition
    script = {"decision": decision, "selected_candidate_id": None,
              "deferred_candidate_ids": [], "short_rationale": "stub: due option window"}
    if later_item is not None:
        script["later_item"] = dict(later_item)
    cog.script = script
    cog.select_source_ref = str(select_source_ref or "")
    before = cog.call_count
    record = chain.runner.run_due(act=True)
    asked = cog.call_count > before
    candidates = list(cog.last_candidates) if asked else []
    cog.select_source_ref = ""
    cog.script = {}
    return record.to_dict(), candidates, asked


# ---------------------------------------------------------------------------------------------
# 安全投影：只保留 ID/枚举/计数/标志，绝不回显标题或正文
# ---------------------------------------------------------------------------------------------


def safe_options(options) -> list[dict]:
    return [{
        "option_ref": str(row.get("option_ref") or ""),
        "kind": str(row.get("kind") or ""),
        "basis_ref": str(row.get("basis_ref") or ""),
        "source_refs": [str(ref) for ref in (row.get("source_refs") or [])],
    } for row in (options or [])]


def safe_candidates(candidates) -> list[dict]:
    return [{
        "candidate_id": str(row.get("candidate_id") or ""),
        "candidate_kind": str(row.get("candidate_kind") or ""),
        "source_kind": str(row.get("source_kind") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "target_refs": [str(ref) for ref in (row.get("target_refs") or [])],
        "legal_decisions": [str(code) for code in (row.get("legal_decisions") or [])],
    } for row in (candidates or [])]


def work_ref(value) -> str:
    text = str(value or "")
    return text if text.startswith("artifact:") else ("artifact:%s" % text if text else "")


def item_status(chain, item_ref: str) -> str:
    row = chain.cycle.future_items.get(str(item_ref or ""))
    return str(getattr(row, "status", "") or "")


def due_refs_at(chain, moment: datetime) -> list[str]:
    return [str(row.get("item_ref") or "")
            for row in chain.cycle.due_future_items(iso(moment))]


def seed_head(chain) -> str:
    seed = str(chain.data["artifact_id"])
    point = chain.port.resume_point(seed)
    return str(getattr(point, "head_version_id", "") or "")


def foreground(chain) -> str:
    return str(chain.runtime.canonical_state().get("foreground_activity_ref") or "")


def _find_item(chain, title: str) -> dict:
    return next((row.to_dict() for row in chain.cycle.future_items.open_items()
                 if str(row.title or "") == title), {})


def _control_scene(tag: str) -> dict:
    """场景①：没有 review / question 时，只有这条无对象 due。"""

    chain = build_chain(tag)
    facts: dict = {"refs": {}, "control": {}}
    try:
        seed = work_ref(chain.data["artifact_id"])
        facts["refs"]["seed_artifact"] = seed
        first, _c1, asked1 = window(chain, at(0), decision="NO_ACTION",
                                    later_item={"title": DUE_A_TITLE, "in_minutes": 5})
        if first.get("decision") != "NO_ACTION":
            facts["blocker"] = {"phase": "control_create_due", "status": first.get("status"),
                                "decision": first.get("decision"),
                                "detail": str(first.get("detail") or "")[:160]}
            return facts
        item_a = _find_item(chain, DUE_A_TITLE)
        facts["refs"]["item_a"] = str(item_a.get("item_ref") or "")
        if not facts["refs"]["item_a"]:
            facts["blocker"] = {"phase": "control_due_missing", "asked": bool(asked1)}
            return facts

        due_round, due_candidates, due_asked = window(chain, at(5), decision="NO_ACTION")
        control_options = safe_options(chain.cycle.last_project_options)
        option_a = next((row["option_ref"] for row in control_options
                         if row["basis_ref"] == facts["refs"]["item_a"]), "")
        source_option = safe_options(lpo.build_options(
            due_items=chain.cycle.due_future_items(iso(at(5))), limit=1))
        facts["refs"]["option_a"] = option_a
        facts["control"].update({
            "asked": bool(due_asked),
            "status": str(due_round.get("status") or ""),
            "decision": str(due_round.get("decision") or ""),
            "due_refs": due_refs_at(chain, at(5)),
            "options": control_options,
            "candidates": safe_candidates(due_candidates),
            "item_a_status": item_status(chain, facts["refs"]["item_a"]),
            "item_a_source_refs": [str(ref) for ref in (item_a.get("source_refs") or [])],
            "due_option_from_source": source_option,
            "writer_calls": int(chain.writer.call_count),
            "seed_head": seed_head(chain),
        })
        return facts
    finally:
        _last_close.clear()
        _last_close.update(close_chain(chain))


def _competition_scene(tag: str) -> dict:
    """场景②：同一隔离根里 review + question 真占满两个名额，再看 due 的选项与候选。"""

    chain = build_chain(tag)
    facts: dict = {"refs": {}, "competition": {}, "experience": {}}
    try:
        seed = work_ref(chain.data["artifact_id"])
        facts["refs"]["seed_artifact"] = seed
        # 真实研究材料（操作者导入的测试文本）：让 question 研究选项真的可创建。
        material = import_research_material(chain)
        facts["refs"]["material"] = "%s@%s" % (material["artifact_id"],
                                               material["source_revision"])
        # 两条真实 private items unclear 标记：在 cycle 第一次读取前写入，正常入口可见。
        mark_unclear(chain, marker_id="unclear:competition:1", note=QUESTION_1_NOTE)
        mark_unclear(chain, marker_id="unclear:competition:2", note=QUESTION_2_NOTE)

        first, _c1, asked1 = window(chain, at(0), decision="NO_ACTION",
                                    later_item={"title": DUE_A_TITLE, "in_minutes": 5})
        item_a = _find_item(chain, DUE_A_TITLE)
        facts["refs"]["item_a"] = str(item_a.get("item_ref") or "")
        q_options = [row for row in safe_options(chain.cycle.last_project_options)
                     if row["kind"] != "FUTURE_ITEM_DUE"]
        if not facts["refs"]["item_a"] or len(q_options) < 2:
            facts["blocker"] = {"phase": "competition_setup",
                                "asked": bool(asked1),
                                "item_a": facts["refs"]["item_a"],
                                "options": safe_options(chain.cycle.last_project_options)}
            return facts
        facts["refs"]["question_option"] = q_options[0]["option_ref"]

        # 到点窗口：due A 已到期；先用 question 选项做一次真实写入，产生可读经历。
        write_round, _c2, asked2 = window(
            chain, at(5), decision="START",
            select_source_ref=facts["refs"]["question_option"])
        experiences = [dict(row) for row in chain.cycle.practice.experiences()]
        facts["refs"]["experience_ids"] = [str(row.get("experience_id") or "")
                                           for row in experiences]
        facts["experience"] = {
            "asked": bool(asked2),
            "status": str(write_round.get("status") or ""),
            "decision": str(write_round.get("decision") or ""),
            "outcome": str(write_round.get("outcome") or ""),
            "decision_route": str(write_round.get("decision_route") or ""),
            "reason_codes": [str(code) for code in (write_round.get("reason_codes") or [])],
            "action_status": str(write_round.get("action_status") or ""),
            "detail": str(write_round.get("detail") or "")[:160],
            "select_missed": str(chain.cognition.select_missed or ""),
            "readable": len(experiences),
        }
        if not experiences:
            facts["blocker"] = {"phase": "no_readable_experience",
                                "status": write_round.get("status"),
                                "decision": write_round.get("decision"),
                                "handoff": str(write_round.get("experience_handoff_error") or "")[:160]}
            return facts

        # 结束前台（真实 ABANDON）；随后建一条带作品对象的 due B。
        end_round, _c3, _a3 = window(chain, at(6), decision="ABANDON")
        facts["experience"]["end_status"] = str(end_round.get("status") or "")
        facts["experience"]["foreground_after_end"] = foreground(chain)
        _b, _c4, asked4 = window(chain, at(7), decision="NO_ACTION",
                                 later_item={"title": DUE_B_TITLE, "in_minutes": 1,
                                             "source_refs": [seed]})
        item_b = _find_item(chain, DUE_B_TITLE)
        facts["refs"]["item_b"] = str(item_b.get("item_ref") or "")
        if not facts["refs"]["item_b"]:
            facts["blocker"] = {"phase": "competition_due_b_missing",
                                "asked": bool(asked4),
                                "options": safe_options(chain.cycle.last_project_options)}
            return facts

        writer_before = int(chain.writer.call_count)
        head_before = seed_head(chain)
        foreground_before = foreground(chain)

        # 竞争窗口：due A/B 都到期，review + question 都在。
        comp_round, comp_candidates, comp_asked = window(chain, at(8), decision="NO_ACTION")
        comp_options = safe_options(chain.cycle.last_project_options)
        candidates = safe_candidates(comp_candidates)
        candidate_sources = {row["source_ref"] for row in candidates}
        basis_refs = {row["basis_ref"] for row in comp_options}
        view = chain.cycle.future_items_view(now=iso(at(8)))
        facts["competition"].update({
            "asked": bool(comp_asked),
            "status": str(comp_round.get("status") or ""),
            "decision": str(comp_round.get("decision") or ""),
            "due_refs": due_refs_at(chain, at(8)),
            "options": comp_options,
            "candidates": candidates,
            "item_a_status": item_status(chain, facts["refs"]["item_a"]),
            "item_b_status": item_status(chain, facts["refs"]["item_b"]),
            "writer_calls_delta": int(chain.writer.call_count) - writer_before,
            "seed_head_same": seed_head(chain) == head_before,
            "foreground_same": foreground(chain) == foreground_before,
            "a_option_in_source": facts["refs"]["item_a"] in basis_refs,
            "a_item_in_candidates": facts["refs"]["item_a"] in candidate_sources,
            "popt_sources": sorted(ref for ref in candidate_sources if ref.startswith("popt:")),
            "option_refs": sorted(row["option_ref"] for row in comp_options),
            "fitem_b_candidate": next((row for row in candidates
                                       if row["source_ref"] == facts["refs"]["item_b"]), None),
            "due_view": {"open_count": int(view.get("open_count") or 0),
                         "due_refs": [str(ref) for ref in (view.get("due_refs") or [])]},
        })
        facts["competition"]["pre_clip_derived"] = (len(comp_options)
                                                    + len(facts["competition"]["due_refs"]))
        facts["competition"]["limit"] = LIMIT
        facts["competition"]["clipped_derived"] = (
            facts["competition"]["pre_clip_derived"] > LIMIT)
        return facts
    finally:
        _last_close.clear()
        _last_close.update(close_chain(chain))


def run_competition(tag: str = "run") -> dict:
    """两个隔离场景先后跑；任一场景的具名阻点原样返回，不修改条件求绿。"""

    control = _control_scene("%s-control" % tag)
    competition = _competition_scene("%s-competition" % tag)
    facts = {"control": control.get("control", {}),
             "competition": competition.get("competition", {}),
             "experience": competition.get("experience", {}),
             "refs": {**control.get("refs", {}), **competition.get("refs", {})}}
    blocker = control.get("blocker") or competition.get("blocker")
    if blocker:
        facts["blocker"] = blocker
    return facts


_last_close: dict = {}


def last_close() -> dict:
    return dict(_last_close)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (real Supply socket); run in WSL/Linux")
        return 0
    try:
        facts = run_competition("smoke")
    except BaseException as exc:  # 真失败如实报告
        print("SMOKE_ABORTED", type(exc).__name__, str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    closure = last_close()
    print("SAFE_FACTS", json.dumps(facts, ensure_ascii=False, default=str)[:6000])
    print("CLOSE", json.dumps(closure, ensure_ascii=False, default=str))
    if facts.get("blocker"):
        print("BLOCKER", json.dumps(facts["blocker"], ensure_ascii=False, default=str))
        print("RESULT: BLOCKED")
        return 1
    control = facts["control"]
    comp = facts["competition"]
    wanted_control = (
        control.get("asked") is True
        and control.get("status") == "NO_ACTION"
        and control.get("decision") == "NO_ACTION"
        and facts["refs"].get("item_a") in (control.get("due_refs") or [])
        and [row["option_ref"] for row in control.get("options") or []]
        == [facts["refs"].get("option_a")]
        and [row["option_ref"] for row in control.get("due_option_from_source") or []]
        == [facts["refs"].get("option_a")]
        and not (control.get("item_a_source_refs") or [])
        and any(row["source_ref"] == facts["refs"].get("option_a")
                and "START" in (row.get("legal_decisions") or [])
                for row in control.get("candidates") or []))
    wanted_comp = (
        comp.get("asked") is True
        and comp.get("status") == "NO_ACTION"
        and comp.get("decision") == "NO_ACTION"
        and facts["refs"].get("item_a") in (comp.get("due_refs") or [])
        and facts["refs"].get("item_b") in (comp.get("due_refs") or [])
        and facts["refs"].get("item_a") in (comp.get("due_view", {}).get("due_refs") or [])
        and comp.get("a_option_in_source") is False
        and comp.get("a_item_in_candidates") is False
        and comp.get("popt_sources") == comp.get("option_refs")
        and len(comp.get("options") or []) == LIMIT
        and comp.get("fitem_b_candidate") is not None
        and facts["refs"].get("seed_artifact")
        in ((comp.get("fitem_b_candidate") or {}).get("target_refs") or [])
        and comp.get("item_a_status") == "OPEN"
        and comp.get("item_b_status") == "OPEN"
        and comp.get("writer_calls_delta") == 0
        and comp.get("seed_head_same") is True
        and comp.get("foreground_same") is True
        and comp.get("clipped_derived") is True)
    checks = [("control: only the due item is offered and START is legal", wanted_control),
              ("competition: review+question fill the two slots; due A is clipped", wanted_comp)]
    failed = 0
    for name, ok in checks:
        print("CHECK %-72s %s" % (name, "PASS" if ok else "FAIL"))
        failed += 0 if ok else 1
    print("SUMMARY checks=%d failed=%d" % (len(checks), failed))
    if failed:
        print("SMOKE_FAILED")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
