#!/usr/bin/env python3
"""零模型真实 owner 冒烟：结果 → 本轮真实使用的做法 → 有证据评价 → 下一轮可见。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）：

1. 在唯一 ``/tmp`` 根里起**真实** Life Supply owner（自己的 Unix socket，落盘）与
   **真实** M0 Memory（EvidenceStore + controls），装配**真实** ProductionLifeRuntime、
   B 的实践工作台、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、AR-0 受管作品适配器。
   决策回答与内容回答是脚本替身（文件里明示）；不宣称真实模型自然采用。
2. 第一轮：真实作品版本写入 → AG-2 handoff 让结果成为可回忆经历；同一轮的正式 Decision
   原始 JSON 里带 ``method_proposal``，经真实 AG-1 校验、提交进 ``DecisionRecord``，再由
   cycle 自己的 ``_save_method_proposal`` 通过真实 owner 存成 TENTATIVE。本冒烟不预建草案、
   不手调 ``_save_method_proposal``，只走"模型原始回答 → AG-1 → DecisionRecord → owner"。
3. 第二轮：Decision 命名该做法并给出 HELPED（证据是 A 真正可读的经历）；真实 owner 写入
   review 版本。断言绑定版本就是本轮提供的那一版、结果 refs 就是 owner 回执里的 review
   版本、评价只把它推进到 TRIAL。
4. 下一轮正式 ``decision_context`` 真的读到 TRIAL；再经既有显式评价入口记一次 NOT_HELPED，
   状态变 CONTESTED、反例计数 1，下一轮同样读到。
5. NO_ACTION 回合仍合法：命名做法但没有 step/结果时，``consequence_refs`` 为空，绝不把
   做法文档自己当成结果。
6. 另一条隔离链：在 Decision 已看到 h1 之后、写作读取之前，让**另一个 owner 写者**真实修订
   做法到 h2。断言绑定仍固定在 h1，评审按 CAS 被拒（REFUSED），方法状态与 trials 不变；
   不会静默改绑到模型没见过的 h2。
7. 所有自有 runtime/服务在 finally 关闭、lease 释放；夹具根保留便于复查。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/result_method_behavior_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
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

TS0 = "2026-11-10T09:00:00+00:00"
NOTE = "为什么写到第三章就卡住？"
REVIEW_BODY = "回顾：第三章卡住是因为没先写结论。"
FIRST_BODY = "第一版正文。"
METHOD_TITLE = "先写结论"
METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否也适用"
FIXTURE_ROOT = Path(os.environ.get("NYAIRO_RESULT_METHOD_FIXTURE_ROOT")
                    or "/tmp/nyairo-result-method-fixtures")

_CHECKS: list[tuple[str, bool, object]] = []


def _artifact_ref(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return "artifact:%s" % text if text else ""


def check(name: str, condition, detail=None) -> None:
    _CHECKS.append((name, bool(condition), detail))
    print("CHECK %-58s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:1200])


class ScriptedCognition:
    """The decision model, scripted: it answers raw JSON and real AG-1 validates it."""

    model_id = "stub:result-method"
    model_version = "result-method-v1"

    def __init__(self) -> None:
        self.call_count = 0
        self.script = None

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.script is not None:
            raw = dict(self.script)
            raw.setdefault("reason_codes", reason)
            self.script = None
        else:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": reason, "short_rationale": "stub: nothing to do",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:result-method:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class ScriptedClient:
    """The content model, scripted: one fixed JSON edit; no credential, no network."""

    def __init__(self) -> None:
        self.payload = {"kind": "append_paragraph", "text": FIRST_BODY,
                        "self_note": "stub: write"}
        self.prompts: list = []

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.prompts.append({"system": system_prompt, "user": user_prompt})
        return ModelCallResult(purpose="chat", provider="stub", model="stub",
                               text=json.dumps(self.payload, ensure_ascii=False),
                               latency_ms=1)


def _fresh_root(tag: str) -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / ("%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid(), tag))
    if root.exists():
        raise SystemExit("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def build_chain(tag: str):
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
                                   subject="test:result-method", title="Result method fixture",
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
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], cognition=cognition, client=client,
                               data=data, env=env, port=port, memory_port=memory_port,
                               evidence_db=evidence_db, controls_db=controls_db)
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

    MemoryControlLedger(str(chain.controls_db), str(chain.evidence_db), owner="chiyo",
                        conversation=life_conversation_id("chiyo")).initialize()
    service = rws.research_from_env(chain.port, dict(chain.env))
    if service is None:
        raise SystemExit("research workspace did not resolve from env")
    answer = service.import_material(
        title="田野笔记", text="第一章：村里的水井在旱季仍然有水。",
        source_kind="user_supplied", origin="操作者导入的文本（口述记录）",
        operation_key="op:material:result-method:1")
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
                                note=NOTE, marker_id="unclear:result-method:1",
                                operation_key="op:unclear:result-method:1")
    if not getattr(result, "ok", True):
        raise SystemExit("mark_unclear failed: %r" % (result,))


def select_option(chain, *, review: bool) -> str:
    options = chain.cycle.project_options()
    wanted = [row for row in options if bool(row.get("review")) is review]
    if not wanted:
        raise SystemExit("no %s option: %r" % ("review" if review else "project", options))
    option_ref = str(wanted[0]["option_ref"])
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=TS0).candidate_refs
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


def end_foreground(chain, *, tag: str) -> dict:
    chain.cognition.script = {"decision": "ABANDON", "selected_candidate_id": None,
                              "deferred_candidate_ids": []}
    return chain.cycle.evaluate_once(trigger_ref="evt:result-method:end-%s" % tag, act=True)


def seed_method_after_first_write(chain):
    """Round one: a real confirmed write *and* a Decision-proposed practice draft.

    The proposal travels as the model's own raw JSON field; real AG-1 validates it, real AG-2
    commits the DecisionRecord, and the cycle's own ``_save_method_proposal`` asks the real
    practice owner to save it.  No draft is pre-created here and the settlement method is never
    called directly.  The proposal cites no experience because none exists yet when the decision
    is taken -- the scope/unknown fields are what make it bounded, and evidence is required later,
    when a verdict is recorded.
    """

    material_id, revision = import_material(chain)
    write_question(chain, material_id, revision)
    chain.client.payload = {"kind": "append_paragraph", "text": FIRST_BODY,
                            "self_note": "stub: write first version"}
    select_option(chain, review=False)
    chain.cognition.script["method_proposal"] = {
        "title": METHOD_TITLE, "instruction": METHOD_INSTRUCTION,
        "scope": METHOD_SCOPE, "uncertainty": METHOD_UNCERTAINTY}
    out = chain.cycle.evaluate_once(trigger_ref="evt:result-method:1", act=True)
    experiences = chain.cycle.practice.experiences()
    if not experiences:
        raise SystemExit("the confirmed write never became a readable experience: %r" % (
            out.get("experience_handoff") or out.get("experience_handoff_error"),))
    experience_id = str(experiences[0]["experience_id"])
    created = out.get("method_created") or {}
    check("the Decision's own proposal is saved by the real owner as TENTATIVE",
          bool(created.get("artifact_id"))
          and created.get("method_state") == "TENTATIVE"
          and created.get("decision_id"), created)
    check("the proposal did not replace the round's real step",
          out.get("outcome") == "ACTED", {key: out.get(key) for key in
                                          ("outcome", "detail", "practice_error")})
    listed = chain.cycle.practice.methods(query="")
    check("the saved method is the one the next round can be offered",
          [row.get("method_ref") for row in listed] == [created.get("method_ref")],
          {"created": created, "listed": listed})
    check("the write result really became a recallable experience",
          bool(experience_id), out.get("experience_handoff"))
    return SimpleNamespace(method_id=str(created["artifact_id"]),
                           method_ref=str(created["method_ref"]),
                           experience_id=experience_id,
                           round_out=out)


def use_method(chain, method, *, concurrent_revision: bool = False):
    """Round two: the Decision names the offered method; the owner writes a real review."""

    end_foreground(chain, tag="before-use")
    select_option(chain, review=True)
    offered_head = chain.cycle.practice.read_method(method.method_id)["head_version_id"]

    if concurrent_revision:
        # Simulate another owner writer revising the method *after* the Decision's own read
        # (``_round_methods`` is already fixed) and *before* the writing prompt reads methods
        # again.  Only the timing of the write is injected; the revision goes through the real
        # service and its CAS, and no product code is changed.
        real_writing = chain.cycle.practice_writing_context
        state = {"revised": False}

        def writing_with_concurrent_revision(artifact_id=""):
            if not state["revised"]:
                revised = chain.cycle.practice.service.revise_method(
                    method.method_id, instruction="并发改过的做法",
                    expected_head=offered_head,
                    operation_key="practice:revise:concurrent")
                state["revised"] = bool(revised.get("ok"))
            return real_writing(artifact_id)

        chain.cycle.practice_writing_context = writing_with_concurrent_revision

    chain.cognition.script.update({
        "method_ref": method.method_ref,
        "method_trial": {"method_ref": method.method_ref, "outcome": "HELPED",
                         "detail": "这次先写结论，确实顺一些",
                         "experience_ids": [method.experience_id]}})
    chain.client.payload = {"kind": "append_paragraph", "text": REVIEW_BODY,
                            "self_note": "stub: write review"}
    out = chain.cycle.evaluate_once(trigger_ref="evt:result-method:2", act=True)
    return offered_head, out


def next_round_method(chain, method_ref: str):
    context = chain.cycle.decision_situation_context(now=TS0)
    rows = [row for row in (context.get("practice") or {}).get("methods") or []
            if row.get("method_ref") == method_ref]
    return rows[0] if rows else None


def success_chain() -> None:
    print("\n== chain A: method used -> real owner result -> evidence-backed trial ==")
    chain = build_chain("success")
    try:
        method = seed_method_after_first_write(chain)
        offered_head, out = use_method(chain, method)

        usage = out.get("method_usage") or {}
        trial = out.get("method_trial") or {}
        receipt = out.get("step_receipt") or {}
        review_id = str(receipt.get("artifact_id") or "")
        check("bound method is the one the Decision named",
              usage.get("artifact_id") == method.method_id, usage)
        check("bound version is the head this round offered",
              usage.get("version_ref") == offered_head,
              {"usage": usage, "offered_head": offered_head})
        check("consequence refs name the real review artifact",
              _artifact_ref(review_id) in (usage.get("consequence_refs") or []),
              {"usage": usage, "receipt": receipt})
        check("consequence refs carry the owner's written version",
              any(str(ref).startswith("artifactversion:") for ref in
                  (usage.get("consequence_refs") or [])), usage)
        check("the method document is not named as its own result",
              method.method_id not in (usage.get("consequence_refs") or []), usage)
        check("HELPED reaches TRIAL with readable evidence",
              trial.get("outcome") == "HELPED" and trial.get("method_state") == "TRIAL"
              and trial.get("evidence_usable") is True, trial)

        after = next_round_method(chain, method.method_ref)
        check("next decision context really reads TRIAL",
              bool(after) and after.get("method_state") == "TRIAL", after)

        # The existing explicit evaluation entry (no extra model call): a counterexample is kept.
        current = chain.cycle.practice.read_method(method.method_id)
        counter = chain.cycle.practice.record_trial(
            artifact_id=method.method_id, outcome="NOT_HELPED",
            detail="这次先写结论反而卡住",
            experience_ids=[method.experience_id],
            expected_head=current["head_version_id"])
        check("a counterexample moves the method to CONTESTED",
              counter.get("method_state") == "CONTESTED"
              and counter.get("evidence_usable") is True, counter)
        after = next_round_method(chain, method.method_ref)
        check("next decision context reads CONTESTED and the counterexample count",
              bool(after) and after.get("method_state") == "CONTESTED"
              and int(after.get("counterexample_count") or 0) == 1, after)

        # A quiet round may still name the method, but it produced no result.
        chain.cognition.script = {"decision": "NO_ACTION", "selected_candidate_id": None,
                                  "deferred_candidate_ids": [], "method_ref": method.method_ref,
                                  "short_rationale": "stub: no action this round"}
        quiet = chain.cycle.evaluate_once(trigger_ref="evt:result-method:quiet", act=True)
        quiet_usage = quiet.get("method_usage") or {}
        check("NO_ACTION stays a legal outcome", quiet.get("outcome") == "NO_ACTION",
              {key: quiet.get(key) for key in ("outcome", "detail", "decision")})
        check("a no-step round names no consequence",
              quiet_usage.get("consequence_refs") == [], quiet_usage)
        check("...and never falls back to the method document",
              method.method_id not in (quiet_usage.get("consequence_refs") or []), quiet_usage)
        check("a no-step round writes no trial", "method_trial" not in quiet, quiet)

        stored = chain.cycle.practice.read_method(method.method_id)
        check("state after the quiet round is unchanged",
              stored.get("method_state") == "CONTESTED" and len(stored.get("trials") or []) == 2,
              {"method_state": stored.get("method_state"),
               "trials": stored.get("trials")})
    finally:
        close_chain(chain)


def stale_chain() -> None:
    print("\n== chain B: a newer head must not be silently rebound ==")
    chain = build_chain("stale")
    try:
        method = seed_method_after_first_write(chain)
        offered_head, out = use_method(chain, method, concurrent_revision=True)
        usage = out.get("method_usage") or {}
        trial = out.get("method_trial") or {}
        stored = chain.cycle.practice.read_method(method.method_id)

        check("the concurrent owner revision really moved the head",
              stored.get("head_version_id") != offered_head,
              {"stored_head": stored.get("head_version_id"), "offered": offered_head})
        check("binding stays on the version this round offered",
              usage.get("version_ref") == offered_head, usage)
        check("a verdict about the unseen head is REFUSED",
              trial.get("status") == "REFUSED", trial)
        check("the refused verdict leaves the owner's trials untouched",
              stored.get("trials") == [], stored.get("trials"))
        check("...and the method state is whatever the revision left (TENTATIVE)",
              stored.get("method_state") == "TENTATIVE", stored.get("method_state"))
    finally:
        close_chain(chain)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0
    print("fixture root:", FIXTURE_ROOT)
    success_chain()
    stale_chain()
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
