#!/usr/bin/env python3
"""零模型真实 owner 冒烟：有界评价进入下一轮决定，表达来源带上已核实的名字。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）：

1. 在唯一 ``/tmp`` 根里起**真实** Life Supply owner（自己的 Unix socket，落盘）、**真实**
   M0 Memory（EvidenceStore + controls），装配**真实** ProductionLifeRuntime、B 的实践工作台
   与分享服务、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、AR-0 受管作品适配器。
   决策回答与内容回答是脚本替身（文件里明示），不宣称真实模型自然采用。
2. 第一轮走正式 ``evaluate_once``：真实作品版本写入 → AG-2 handoff 让结果成为可回忆经历；
   同一轮正式 Decision 的 ``method_proposal`` 经 AG-1 校验并由 cycle 保存成 TENTATIVE。
3. 用**真实证据门**给这条做法记一次 ``NOT_HELPED``（不是写成功，也不是手造 CONTESTED）。
4. 下一轮正式 Decision 的请求结构里：practice 方法行带 ``trial_count`` /
   ``counterexample_count`` 与最近一条显式评价（outcome / 受评版本 / 证据条数，无 detail
   正文）；adapter 白名单如实保留；本轮 owner 详情读取 ≤3 且同轮不重复。
5. 分享走原正式 ``prepare`` → ``defer`` → ``send_local``，只有 1 条本地 receipt；来源作品的
   id 与标题**同一次读取**核对后成对出现；``truncated`` 真值经 adapter 白名单透传（本夹具
   列表没有被截断，因此是显式 False）。
6. 再准备第二份草稿后让来源读被拒：请求发送时 owner 拒绝，head/状态零变、没有 receipt；
   最终请求里草稿正文是**扣留**事实而不是空正文。
7. 重新装配（新的 seam / 同一 owner）读回同样的评价事实，纯读阶段 0 写。
8. **独立根**再走一条正式链：真实 owner 建 6 条 open 草稿（> ``MAX_PROMPT_SHARES``），
   `evaluate_once → AG-1` 的实际请求里 ``sharing.truncated`` 为真、只展示 5 条，6 条草稿
   全部仍在 DRAFT、无 receipt、无自动 drop/send；这是 prompt 自己的界，不是 owner 列表界。
9. 所有自有 runtime/服务在 finally 关闭、lease 释放；夹具根保留便于复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_results_expression_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

#: Each formal round runs at its own observed instant, exactly like the donor fixture's clock:
#: the quiet ledger keys on the round's state, and a frozen instant would make every later round
#: answer SAME_STATE_ALREADY_CONSIDERED instead of taking the Decision this smoke needs.
_BASE_TIME = datetime.now().astimezone()
_ROUND_INDEX = [0]
TS0 = _BASE_TIME.isoformat()


def next_instant() -> str:
    _ROUND_INDEX[0] += 1
    return (_BASE_TIME + timedelta(minutes=61 * _ROUND_INDEX[0])).isoformat()


NOTE = "为什么写到第三章就卡住？"
FIRST_BODY = "第一版正文。"
READING_TITLE = "山居笔记"
READING_CONTENT = "第一页：石阶上的水痕还没干。\n第二页：风从竹林穿过来。"
REVIEW_BODY = "回顾：第三章卡住是因为没先写结论。"
METHOD_TITLE = "先写结论"
METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否也适用"
SHARE_TEXT = "今天把那页留白重排完了，第二段还是挤，但这样更像当天。"
SECOND_SHARE_TEXT = "这句话先留在这里，等一个安静的时候再说。"
TRUNCATION_SHARE_TEXT = "这一段先留在这里，等第%d次再接着说。"
FIXTURE_ROOT = Path(os.environ.get("NYAIRO_V05_RESULTS_FIXTURE_ROOT")
                    or "/tmp/nyairo-v05-results-expression-fixtures")

_CHECKS: list[tuple[str, bool, object]] = []


def _artifact_ref(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return "artifact:%s" % text if text else ""


def check(name: str, condition, detail=None) -> None:
    _CHECKS.append((name, bool(condition), detail))
    print("CHECK %-62s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:1200])


class ScriptedCognition:
    """The decision model, scripted: raw JSON through real AG-1; captures the real request."""

    model_id = "stub:v05-results"
    model_version = "v05-results-v1"

    def __init__(self) -> None:
        self.call_count = 0
        self.script = None
        self.plan: list[str] = []
        self.captured: list[dict] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_cognition_adapter as aa
        import agency_decision_ag1 as ag1

        self.call_count += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        sharing = situation.get("sharing") if isinstance(situation, dict) else {}
        sources = list((sharing or {}).get("sources") or [])
        shares = list((sharing or {}).get("shares") or [])
        self.captured.append({
            "practice": json.loads(json.dumps(situation.get("practice") or {}, default=str)),
            "sharing": json.loads(json.dumps(sharing or {}, default=str)),
            "bounded": aa.ProductionAgencyCognitionAdapter._bounded_situation(situation),
        })
        reason = allowed[:1] or ["NO_ACTION"]
        step = self.plan.pop(0) if self.plan else ""
        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": reason, "short_rationale": "stub: quiet round",
                     "deferred_candidate_ids": []}
        if step in ("prepare", "prepare_second") and sources:
            raw["sharing"] = {
                "op": "prepare",
                "source_artifact_id": sources[0]["artifact_id"],
                "share_text": SHARE_TEXT if step == "prepare" else SECOND_SHARE_TEXT,
                "title": "留白重排" if step == "prepare" else "再想一句",
            }
        elif step == "defer" and shares:
            share = shares[0]
            raw["sharing"] = {"op": "defer", "share_id": share["share_id"],
                              "expected_head": share["head_version_id"],
                              "opportunity_ref": "quiet-evening"}
        elif step == "send_local" and shares:
            share = shares[0]
            raw["sharing"] = {"op": "send_local", "share_id": share["share_id"],
                              "expected_head": share["head_version_id"]}
        if self.script is not None:
            raw.update(dict(self.script))
            self.script = None
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:v05-results:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class ScriptedClient:
    """The content model, scripted: one fixed JSON edit; no credential, no network."""

    def __init__(self) -> None:
        self.payload = {"kind": "append_paragraph", "text": FIRST_BODY,
                        "self_note": "stub: write"}

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        return ModelCallResult(purpose="chat", provider="stub", model="stub",
                               text=json.dumps(self.payload, ensure_ascii=False),
                               latency_ms=1)


class SourceRefusingPort:
    """The real owner port, except one **source** artifact read is refused."""

    def __init__(self, inner, *, source_id: str) -> None:
        self._inner = inner
        self._source = str(source_id or "").replace("artifact:", "")

    def read_project(self, artifact_id, **kwargs):
        if str(artifact_id or "").replace("artifact:", "") == self._source:
            return {"ok": False, "status": "rejected", "reason_code": "SOURCE_GOVERNED",
                    "detail": "the owner refused this source read"}
        return self._inner.read_project(artifact_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class CountingPractice:
    """The real practice service; counts detail reads and writes for the pure-read phases."""

    WRITES = ("create_review", "create_method", "record_trial", "revise_method", "retire_method")

    def __init__(self, service) -> None:
        self._service = service
        self.read_method_calls: list[str] = []
        self.write_calls: list[str] = []

    def __getattr__(self, name):
        attribute = getattr(self._service, name)
        if name == "read_method":
            def counted(artifact_id):
                self.read_method_calls.append(str(artifact_id))
                return attribute(artifact_id)
            return counted
        if name in self.WRITES:
            def counted(*args, **kwargs):
                self.write_calls.append(name)
                return attribute(*args, **kwargs)
            return counted
        return attribute


def _fresh_root(tag: str) -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / ("%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid(), tag))
    if root.exists():
        raise SystemExit("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def build_chain(tag: str, *, reading_title: str = "", reading_content=None):
    """One fully isolated real-owner chain; returns its objects (caller closes)."""

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
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter
    from tests.test_one_day_life import ENV_GATES, Services

    root = _fresh_root(tag)
    services = Services(root / "supply")
    runtime = None
    try:
        package_dir = root / "package"
        schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
        world_home = root / "world-home"
        init_instance(package_dir, world_home)
        instance = root / "instance"
        with mock.patch.dict(os.environ, ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=services.supply_path,
                                   governance_socket=services.gov_path,
                                   subject="test:v05-results", title="V05 results fixture",
                                   world_package=package_dir, create_private_items=True,
                                   reading_title=reading_title,
                                   reading_content=reading_content)
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
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
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
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        if (assembled.get("practice") or {}).get("status") != "READY":
            raise SystemExit("practice workspace not READY: %r" % (assembled.get("practice"),))
        if (assembled.get("sharing") or {}).get("status") != "READY":
            raise SystemExit("sharing workspace not READY: %r" % (assembled.get("sharing"),))
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], cognition=cognition, client=client,
                               data=data, env=env, port=port, memory_port=memory_port,
                               assembled=assembled)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        services.stop()
        raise


def close_chain(chain) -> None:
    try:
        chain.runtime.close()
    except Exception as exc:  # a close failure must be printed, not hidden
        print("  runtime close error:", type(exc).__name__, str(exc)[:200])
    try:
        chain.services.stop()
    except Exception as exc:
        print("  supply stop error:", type(exc).__name__, str(exc)[:200])


def import_material(chain):
    import research_workspace_service as rws
    from life_experience_port import life_conversation_id
    from memory_control import MemoryControlLedger

    MemoryControlLedger(str(chain.env["NYA02_MEMORY_CONTROLS_DB"]),
                        str(chain.env["NYA02_MEMORY_DB"]), owner="chiyo",
                        conversation=life_conversation_id("chiyo")).initialize()
    service = rws.research_from_env(chain.port, dict(chain.env))
    if service is None:
        raise SystemExit("research workspace did not resolve from env")
    answer = service.import_material(
        title="田野笔记", text="第一章：村里的水井在旱季仍然有水。",
        source_kind="user_supplied", origin="操作者导入的文本（口述记录）",
        operation_key="op:material:v05-results:1")
    if not answer.get("ok"):
        raise SystemExit("material import failed: %r" % (answer,))
    material = answer["data"]["material"]
    return str(material["artifact_id"]), str(material["source_revision"])


def write_question(chain, material_id: str, revision: str) -> None:
    import private_daily_items as pdi

    items_id = str(chain.data["private_items_artifact_id"])
    store = pdi.PrivateDailyItems(chain.port, items_id,
                                  workspace_id=str(chain.data.get("workspace_id") or ""),
                                  grant_id=str(chain.data["artifact_grant_id"]))
    result = store.mark_unclear(source_id=material_id, source_revision=revision,
                                note=NOTE, marker_id="unclear:v05-results:1",
                                operation_key="op:unclear:v05-results:1")
    if not getattr(result, "ok", True):
        raise SystemExit("mark_unclear failed: %r" % (result,))


def select_option(chain, *, review: bool, observed_at: str = TS0) -> str:
    options = chain.cycle.project_options()
    wanted = [row for row in options if bool(row.get("review")) is review]
    if not wanted:
        raise SystemExit("no %s option: %r" % ("review" if review else "project", options))
    option_ref = str(wanted[0]["option_ref"])
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at).candidate_refs
    selected = None
    for candidate_ref in candidates:
        payload = chain.runtime.candidate_read.get_candidate(str(candidate_ref))
        if str((payload or {}).get("source_ref")) == option_ref:
            selected = str(candidate_ref)
            break
    if selected is None:
        raise SystemExit("the option never became a candidate: %s" % option_ref)
    chain.cognition.script = {"decision": "START", "selected_candidate_id": selected,
                              "deferred_candidate_ids": []}
    return option_ref


def first_round(chain):
    """R1: a real write, a real experience, and a Decision-proposed practice draft."""

    material_id, revision = import_material(chain)
    write_question(chain, material_id, revision)
    chain.client.payload = {"kind": "append_paragraph", "text": FIRST_BODY,
                            "self_note": "stub: write first version"}
    stamp = next_instant()
    select_option(chain, review=False, observed_at=stamp)
    chain.cognition.script["method_proposal"] = {
        "title": METHOD_TITLE, "instruction": METHOD_INSTRUCTION,
        "scope": METHOD_SCOPE, "uncertainty": METHOD_UNCERTAINTY}
    out = chain.cycle.evaluate_once(trigger_ref="evt:v05:1", act=True, observed_at=stamp)
    created = out.get("method_created") or {}
    experiences = chain.cycle.practice.experiences()
    if not experiences:
        raise SystemExit("the confirmed write never became a readable experience: %r" % (
            out.get("experience_handoff") or out.get("experience_handoff_error"),))
    experience_id = str(experiences[0]["experience_id"])
    receipt = out.get("step_receipt") or {}
    review_id = str(receipt.get("artifact_id") or "")
    check("the Decision's proposal is saved by the real owner",
          bool(created.get("artifact_id")) and created.get("method_state") == "TENTATIVE",
          created)
    check("the write result is a recallable experience", bool(experience_id), experiences)
    if not review_id:
        raise SystemExit("round one produced no work artifact: %r" % (out.get("outcome"),))
    # Bind the instance to the work it just wrote, exactly like the sharing-chain fixture does:
    # a source must be the work this round is *about*, not a library listing.
    chain.cycle.work_artifact_id = review_id
    return SimpleNamespace(method_id=str(created["artifact_id"]),
                           method_ref=str(created["method_ref"]),
                           experience_id=experience_id,
                           review_id=review_id)


def run_round(chain, step: str, *, trigger: str) -> dict:
    chain.cognition.plan = [step] if step else []
    return chain.cycle.evaluate_once(trigger_ref=trigger, act=True,
                                     observed_at=next_instant())


def main_chain() -> None:
    print("\n== chain: explicit verdict -> next request -> bounded expression ==")
    chain = build_chain("views", reading_title=READING_TITLE,
                        reading_content=READING_CONTENT)
    try:
        method = first_round(chain)

        # The verdict is created through the real evidence gate (not by writing a version).
        head_before_trial = chain.cycle.practice.read_method(
            method.method_id)["head_version_id"]
        recorded = chain.cycle.practice.record_trial(
            artifact_id=method.method_id, outcome="NOT_HELPED",
            detail="这次先写结论反而卡住", experience_ids=[method.experience_id],
            expected_head=head_before_trial)
        check("a real owner records the evidence-backed NOT_HELPED",
              recorded.get("method_state") == "CONTESTED"
              and recorded.get("evidence_usable") is True, recorded)

        counted = CountingPractice(chain.cycle.practice.service)
        chain.cycle.practice.service = counted

        # R2: the next formal Decision request has to carry the counts and the verdict itself.
        # This round also asks to prepare a share, so the request is a real one (the quiet gate
        # would suppress a second identical no-action instant, and rightly so).
        out2 = run_round(chain, "prepare", trigger="evt:v05:2")
        captured = chain.cognition.captured[-1]
        practice = captured["practice"] or {}
        methods = list(practice.get("methods") or [])
        row = [item for item in methods
               if item.get("artifact_id") == method.method_id]
        check("the next request still offers the method", bool(row), methods)
        row = row[0] if row else {}
        check("the list counts travel with the method row",
              row.get("trial_count") == 1 and row.get("counterexample_count") == 1, row)
        check("the method state is CONTESTED in the next request",
              row.get("method_state") == "CONTESTED", row)
        evaluation = row.get("evaluation") or {}
        check("the most recent explicit verdict travels bounded",
              evaluation.get("available") is True
              and evaluation.get("outcome") == "NOT_HELPED"
              and evaluation.get("version_id") == head_before_trial
              and evaluation.get("evidence_count") == 1, evaluation)
        check("the trial's free text never travels", "detail" not in evaluation, evaluation)
        bounded_practice = ((captured["bounded"] or {}).get("practice") or {})
        bounded_row = [item for item in bounded_practice.get("methods") or []
                       if item.get("method_ref") == method.method_ref]
        check("the adapter whitelist keeps the counts and the bounded verdict",
              bool(bounded_row)
              and bounded_row[0].get("trial_count") == 1
              and bounded_row[0].get("counterexample_count") == 1
              and (bounded_row[0].get("evaluation") or {}).get("outcome") == "NOT_HELPED",
              bounded_row)
        delta = list(counted.read_method_calls)
        check("one formal round reads at most three method details",
              0 < len(delta) <= 3, delta)
        check("...and never the same method twice in one round",
              len(delta) == len(set(delta)), delta)
        before = len(counted.read_method_calls)
        chain.cycle.decision_situation_context(now=next_instant())
        check("a second context call in the same round reads no detail again",
              len(counted.read_method_calls) == before,
              counted.read_method_calls[before:])

        # The same request carries the sharing source: id and title from the same owner read.
        block = captured["sharing"] or {}
        work_sources = [item for item in block.get("sources") or []
                        if item.get("artifact_id") == method.review_id]
        check("the work source is offered by its real id", bool(work_sources),
              block.get("sources"))
        owner_view = chain.port.read_artifact(method.review_id)
        owner_title = " ".join(str(((owner_view or {}).get("artifact") or {})
                                  .get("title") or "").split())[:120]
        check("the source title is the one this round read for that same artifact",
              bool(work_sources) and work_sources[0].get("title") == owner_title,
              {"source": work_sources, "owner_title": owner_title})

        # The same request carries the *reading* source from the instance's own binding: the id is
        # the artifact the owner minted at prepare time, and the title comes from this round's
        # already-paid reading-situation read (``refresh=True`` in the round's material facts).
        # The extra ``read_artifact`` below is the smoke's own comparison, not a production read.
        reading_id = str(chain.data.get("reading_artifact_id") or "")
        reading_sources = [item for item in block.get("sources") or []
                           if item.get("kind") == "reading_material"]
        round_material = ((chain.cycle.last_reading_situation or {}).get("material") or {})
        round_reading_title = " ".join(str(round_material.get("title") or "").split())[:120]
        owner_reading = chain.port.read_artifact(reading_id)
        owner_reading_title = " ".join(str(((owner_reading or {}).get("artifact") or {})
                                           .get("title") or "").split())[:120]
        check("the bound reading artifact is offered by its real id",
              bool(reading_id) and len(reading_sources) == 1
              and _artifact_ref(reading_sources[0].get("artifact_id")) == _artifact_ref(reading_id),
              {"reading_id": reading_id, "sources": reading_sources})
        check("the reading source title is this round's own material title",
              bool(reading_sources) and round_reading_title
              and reading_sources[0].get("title") == round_reading_title == owner_reading_title,
              {"source": reading_sources, "round_title": round_reading_title,
               "owner_title": owner_reading_title})
        bounded_reading = [item for item in (((captured["bounded"] or {}).get("sharing")
                                             or {}).get("sources") or [])
                           if item.get("kind") == "reading_material"]
        check("the reading title also survives the prompt whitelist",
              bool(bounded_reading) and bounded_reading[0].get("title") == round_reading_title,
              bounded_reading)
        check("the owner's untruncated list says so explicitly",
              block.get("truncated") is False, block.get("truncated"))
        bounded_sharing = ((captured["bounded"] or {}).get("sharing") or {})
        check("truncated survives the prompt whitelist",
              bounded_sharing.get("truncated") is False, bounded_sharing.get("truncated"))
        prepared = out2.get("sharing") or {}
        check("prepare creates a DRAFT, not a delivery",
              prepared.get("ok") is True and prepared.get("state") == "DRAFT"
              and not prepared.get("delivered"), prepared)

        # R3/R4: defer, then exactly one local receipt.
        out4 = run_round(chain, "defer", trigger="evt:v05:4")
        deferred = out4.get("sharing") or {}
        check("defer records what she is waiting for",
              deferred.get("state") == "DEFERRED"
              and (deferred.get("deferred") or {}).get("opportunity_ref") == "quiet-evening",
              deferred)
        out5 = run_round(chain, "send_local", trigger="evt:v05:5")
        sent = out5.get("sharing") or {}
        receipt = sent.get("receipt") or {}
        check("send_local produces exactly one local receipt",
              sent.get("state") == "LOCAL_DELIVERED" and sent.get("delivered") is True
              and sent.get("delivery_count") == 1 and receipt.get("local_only") is True,
              sent)
        check("nothing external is claimed", receipt.get("external_channel_sent") is False
              and receipt.get("external_delivery_confirmed") is False, receipt)

        # R6: a second draft from the still-readable source.
        out6 = run_round(chain, "prepare_second", trigger="evt:v05:6")
        second = out6.get("sharing") or {}
        check("the second draft is a separate DRAFT",
              second.get("ok") is True and second.get("state") == "DRAFT", second)
        second_id = str(second.get("share_id") or "")

        # R7: the source becomes unreadable -> the drafted words are withheld and the send is
        # refused by the owner with zero writes.
        import life_sharing_context as lsc
        import life_sharing_service as lss

        service = chain.cycle.sharing_service
        governed = lss.LifeSharingService(
            SourceRefusingPort(service.projects, source_id=method.review_id),
            clock=service._clock)
        chain.cycle.sharing_service = governed
        head_before = lsc.read_draft(governed, second_id)["head_version_id"]
        out7 = run_round(chain, "send_local", trigger="evt:v05:7")
        block7 = chain.cognition.captured[-1]["sharing"] or {}
        rows7 = [item for item in block7.get("shares") or []
                 if item.get("share_id") == second_id]
        check("the withheld words are a fact in the final request",
              bool(rows7) and rows7[0].get("text_withheld") is True
              and rows7[0].get("text_available") is False
              and not rows7[0].get("text_excerpt"), rows7)
        refused = out7.get("sharing") or {}
        after = lsc.read_draft(governed, second_id)
        check("the owner refuses the send", refused.get("ok") is False
              and refused.get("delivered") is False, refused)
        check("...with zero writes and no receipt",
              after.get("head_version_id") == head_before
              and after.get("state") == "DRAFT" and after.get("receipt") is None, after)

        # The pure-read phases never wrote to the owner.
        check("the read views wrote nothing to the practice owner",
              counted.write_calls == [], counted.write_calls)

        # Re-assembly: a brand-new seam over the same owner answers the same facts.
        import life_practice_context as lpc

        rebuilt = lpc.PracticeSeam(chain.cycle.practice.service)
        rows = rebuilt.methods(query="")
        found = rebuilt.evaluations_for(rows).get(method.method_id) or {}
        check("a rebuilt exactly-same-owner view reads the same verdict",
              found.get("available") is True and found.get("outcome") == "NOT_HELPED"
              and found.get("version_id") == head_before_trial, found)
        check("the rebuilt view also wrote nothing", counted.write_calls == [],
              counted.write_calls)
    finally:
        close_chain(chain)


def main_truncation_chain() -> None:
    """A separate root: more open drafts than the prompt bound -> the request says so."""

    import life_sharing_context as lsc

    print("\n== chain: prompt-bound truncation over real open drafts ==")
    chain = build_chain("prompt-truncation", reading_title=READING_TITLE,
                        reading_content=READING_CONTENT)
    try:
        service = chain.cycle.sharing_service
        source_id = str(chain.data.get("reading_artifact_id") or "")
        if not source_id:
            raise SystemExit("the instance has no reading artifact to prepare from")
        draft_ids: list[str] = []
        for index in range(lsc.MAX_PROMPT_SHARES + 1):
            prepared = service.prepare_share(
                source_artifact_id=source_id,
                share_text=TRUNCATION_SHARE_TEXT % index,
                title="截断观察%d" % index,
                operation_key="share:op:v05-truncation:%d" % index)
            if not prepared.get("ok"):
                raise SystemExit("prepare failed: %r" % (prepared,))
            draft_ids.append(str(prepared["share_id"]))
        check("six real open drafts were created by the owner", len(draft_ids) == 6, draft_ids)

        owner_list = service.list_shares(include_text=False)
        check("the owner's own list is not cut at six rows",
              owner_list.get("ok") is True
              and owner_list["data"]["truncated"] is False
              and owner_list["data"]["counts"]["total"] == 6,
              (owner_list.get("data") or {}).get("counts"))
        heads_before = {share_id: lsc.read_draft(service, share_id)["head_version_id"]
                        for share_id in draft_ids}

        out = chain.cycle.evaluate_once(trigger_ref="evt:v05:trunc:1", act=True,
                                        observed_at=next_instant())
        check("the formal round really took a Decision",
              chain.cognition.call_count == 1 and out.get("decision") == "NO_ACTION",
              {"calls": chain.cognition.call_count, "decision": out.get("decision")})
        captured = chain.cognition.captured[-1]
        block = captured.get("sharing") or {}
        shown = list(block.get("shares") or [])
        check("the final request says the prompt bound cut an open draft",
              block.get("truncated") is True, block.get("truncated"))
        check("...and shows at most five of the six drafts",
              len(shown) == lsc.MAX_PROMPT_SHARES
              and {row.get("share_id") for row in shown} <= set(draft_ids),
              [row.get("share_id") for row in shown])
        bounded_block = (captured.get("bounded") or {}).get("sharing") or {}
        check("truncated survives the prompt whitelist",
              bounded_block.get("truncated") is True, bounded_block.get("truncated"))

        after_list = service.list_shares(include_text=False)
        after_heads = {share_id: lsc.read_draft(service, share_id)["head_version_id"]
                       for share_id in draft_ids}
        check("all six drafts survive untouched: no drop, no send",
              after_list.get("ok") is True
              and after_list["data"]["counts"]["total"] == 6
              and after_list["data"]["inbox"]["delivered_count"] == 0
              and all(row["state"] == "DRAFT" for row in after_list["data"]["shares"])
              and after_heads == heads_before
              and not (out.get("sharing") or {}),
              {"counts": after_list["data"]["counts"],
               "inbox": after_list["data"]["inbox"]})
    finally:
        close_chain(chain)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0
    print("fixture root:", FIXTURE_ROOT)
    main_chain()
    main_truncation_chain()
    failed = [name for name, ok, _ in _CHECKS if not ok]
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
