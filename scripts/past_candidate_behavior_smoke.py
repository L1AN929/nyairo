#!/usr/bin/env python3
"""零模型真实 owner 冒烟：过去已开始的活动 → 候选自带的 owner 目标 → 下一步准备。

它做什么（本机、0 产品模型、0 外网）
------------------------------------
1. 在全新 ``/tmp`` 根里起**真实** Life Supply owner（独立 Unix socket）与**真实** M0
   （EvidenceStore + 控制账本 + 边界端口），再走 ``one_day_life.prepare`` 的正式实例准备
   命令建出真实 workspace / grant / 种子作品。
2. 用**正式** ``build_production_life_runtime`` + ``assemble_autonomous_life`` 装配 cycle；
   Decision 模型是脚本替身（只回 AG-1 合法集里的 CONTINUE/START JSON，由真实 AG-1 校验），
   内容 writer 是确定性替身；其余 AG-0/AG-2/AR-0/LR-2/Supply 都是真的。
3. 第一轮真实 START 种子作品 → 真实版本 + 真实 AG-2 handoff → 真实 M0 行。Decision 替身
   只在候选的 ``target_refs`` 里做**精确**作品 ref 匹配，并按命名空间排除 closeout 包装：
   同一件作品会同时以继续与收尾两种候选出现，收尾候选的 closeout ref 含有作品 id 的子串，
   不能靠字符串包含或列表顺序选中它。
4. **纯历史对照（同一基线，只读）**：装/不装 continuity 读口，比较正式
   ``_read_resume_point()`` 的选中对象与 ``selected_by``；并读出 C 的
   ``CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`` 与 owner 回执版本。这里只证明「过去证据改变了
   本轮选中的继续对象」，不声称它改变已提交候选的执行目标。
5. **执行目标（本单修复）**：另建一件真实作品 A2 并把它设为本轮绑定作品，关闭 continuity
   读口；第二轮脚本 Decision 选择真实 CURRENT_ACTIVITY 候选（其 target_refs 由真实 LR-2
   Activity 记录给出）。正式 step 应继续 Activity 自己点名的 A1，而不是轮选回退的 A2：
   A1 真的多一版、A2 完全不动；旧行为会把这一版写到 A2。
6. 负控：无历史时 ``selected_by`` 回退到绑定作品（A2），但执行仍走 A1；A2 head 不变；
   没有作品 target 的候选保留 fallback（由 ``tests/test_past_candidate_behavior.py`` 覆盖）。
   所有自有进程/线程在 finally 收尾；scratch 根保留。

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/past_candidate_behavior_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from life_continuity_inputs import (  # noqa: E402
    ContinuityBinding, LifeContinuityInputs)
from life_experience_memory_consumer import (  # noqa: E402
    MemoryAdmissionExperienceConsumer)
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

from tests.test_one_day_life import Services, StubWriter  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_PAST_CANDIDATE_SMOKE_ROOT")
               or "/tmp/nyairo-past-candidate-smoke")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
MEMORY_OWNER = "smoke-memory"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("SMOKE_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:4000])
    raise SystemExit(1)


CLOSEOUT_REF_PREFIX = "closeout:"


def _work_ref_for_seed(value) -> str:
    """The work ref the personal-opportunity source builds from the owner's artifact id.

    The source forms ``f"artifact:{point.artifact_id}"``; the owner's own ``artifact_id`` already
    begins with ``artifact:``, so the offered ref reads ``artifact:artifact:<uuid>``.  A bare uuid
    receives exactly one prefix.  This mirrors the source instead of "cleaning up" a prefix the
    production ref really carries.
    """
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("artifact:artifact:"):
        return text
    return "artifact:" + text


def _candidate_refs(row) -> list[str]:
    return [str(ref) for ref in (row.get("target_refs") or [])]


def _is_closeout_candidate(row) -> bool:
    """A wrap-up offer is a different choice, not a START target for the work itself.

    The closeout item carries ``closeout:artifact:<owner id>`` plus the same
    ``artifact:<owner id>`` the continuation item carries, so existence of the plain ref does
    not by itself identify the work offer; the closeout namespace has to be excluded by name.
    """
    return any(ref.startswith(CLOSEOUT_REF_PREFIX) for ref in _candidate_refs(row))


def _work_start_matches(candidates, seed_artifact) -> list[dict]:
    """Every candidate that is an exact START offer for *this* work.

    Matching is a whole-ref membership test, never a substring: the closeout spelling embeds
    the work id inside ``closeout:artifact:...`` and would win a ``seed in " ".join(refs)``
    test depending on candidate order.
    """
    seed_ref = _work_ref_for_seed(seed_artifact)
    matches: list[dict] = []
    for row in candidates:
        if _is_closeout_candidate(row):
            continue
        if seed_ref not in _candidate_refs(row):
            continue
        if "START" not in (row.get("legal_decisions") or []):
            continue
        matches.append(row)
    return matches


def _work_continue_matches(candidates) -> list[dict]:
    """Every legal CONTINUE_CURRENT offer that is not a wrap-up wrapper."""
    matches: list[dict] = []
    for row in candidates:
        if _is_closeout_candidate(row):
            continue
        if row.get("candidate_kind") != "CONTINUE_CURRENT":
            continue
        if "CONTINUE" not in (row.get("legal_decisions") or []):
            continue
        matches.append(row)
    return matches


class _ScriptedDecisionModel:
    """Decision 模型替身：只从 AG-1 已给出的合法集里挑，返回原始 JSON 交给真实 AG-1。"""

    model_id = "stub:past-candidate-smoke"
    model_version = "past-candidate-smoke-v1"

    def __init__(self, *, seed_artifact: str) -> None:
        self.seed_artifact = str(seed_artifact)
        self.policy = "start_seed"
        self.calls = 0
        self.last_offered: dict = {}
        self.last_selection: dict = {}

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]

        closeout_excluded = [row.get("candidate_id") for row in candidates
                             if _is_closeout_candidate(row)]
        chosen = None
        selection: dict = {"policy": self.policy,
                           "closeout_excluded": closeout_excluded}
        if self.policy == "start_seed":
            matches = _work_start_matches(candidates, self.seed_artifact)
            selection.update({
                "seed_ref": _work_ref_for_seed(self.seed_artifact),
                "work_start_matches": [row.get("candidate_id") for row in matches],
            })
            if len(matches) == 1:
                chosen = (matches[0], "START")
        elif self.policy == "continue_current":
            matches = _work_continue_matches(candidates)
            selection.update({
                "work_continue_matches": [row.get("candidate_id") for row in matches],
            })
            if len(matches) == 1:
                chosen = (matches[0], "CONTINUE")
        self.last_selection = selection
        self.last_offered = {
            "policy": self.policy,
            "selection": selection,
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "target_refs": row.get("target_refs"),
                            "legal_decisions": row.get("legal_decisions")}
                           for row in candidates],
        }
        if chosen is None:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": ["NO_LEGAL_CANDIDATE"],
                   "short_rationale": "stub: no legal candidate for this policy",
                   "deferred_candidate_ids": []}
        else:
            row, word = chosen
            raw = {"decision": word, "selected_candidate_id": row["candidate_id"],
                   "reason_codes": reason, "short_rationale": "stub: %s" % word.lower(),
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:past-candidate-smoke:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def _memory() -> SimpleNamespace:
    root = RUN / "memory"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    evidence = root / "evidence.sqlite"
    controls = root / "controls.sqlite"
    store = EvidenceStore(evidence)
    ledger = MemoryControlLedger(controls, evidence, owner=MEMORY_OWNER,
                                 conversation=life_conversation_id(MEMORY_OWNER))
    ledger.initialize()
    port = LifeExperiencePort(evidence, controls_db=controls, subject=MEMORY_OWNER)
    return SimpleNamespace(store=store, ledger=ledger, port=port,
                           consumer=MemoryAdmissionExperienceConsumer(port, enabled=True),
                           evidence=evidence, controls=controls)


def _continuity(runtime, memory, state_root: Path):
    home = str(state_root)
    now = datetime.now(timezone.utc).isoformat()

    def read():
        return LifeContinuityInputs(
            binding=ContinuityBinding(
                subject=MEMORY_OWNER, home=home, now=now,
                owner_refs={"memory": "memory:%s" % MEMORY_OWNER,
                            "activity": "activity:canonical",
                            "agenda": "agenda:lr4",
                            "artifact": "artifact:supply"}),
            past_source=memory.port,
            activity_source=getattr(runtime, "activity_read", None),
            agenda_source=getattr(runtime, "waiting_coordinator", None),
            governance_probe=lambda: {"healthy": bool(memory.ledger.projection().healthy)},
        )

    return read


def _version(port, artifact_id):
    point = port.resume_point(artifact_id)
    return point.version_number


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    services = Services(RUN / "services")
    memory = _memory()
    runtime = None
    try:
        instance = RUN / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:past-candidate",
            title="Past candidate smoke", seed_content="种子正文第一句。\n",
            memory_db=memory.evidence, memory_controls_db=memory.controls,
            memory_subject=MEMORY_OWNER)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        state_root = odl.life_state_root(instance)
        seed = str(data["artifact_id"])
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecisionModel(seed_artifact=seed)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=memory.consumer)
        writer = StubWriter()
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        cycle.continuity_source = _continuity(runtime, memory, state_root)

        seed_before = _version(port, seed)
        first = cycle.evaluate_once(trigger_ref="evt:past-smoke:1", act=True)
        _check(first.get("outcome") == "ACTED", "round 1 did not act",
               {"outcome": first.get("outcome"), "reason_code": first.get("reason_code"),
                "detail": first.get("detail"), "decision": first.get("decision"),
                "adoption": first.get("adoption"), "error": first.get("error"),
                "material_has": (first.get("material") or {}).get("has_material"),
                "artifact_selection": first.get("artifact_selection"),
                "offered": cognition.last_offered})
        # The stub's choice has to be the *exact* work offer, not whichever candidate happens to
        # list the seed inside a longer ref: the closeout wrapper carries the seed id in its own
        # namespace and used to win whenever it was offered first.
        selection = cognition.last_selection
        start_matches = list(selection.get("work_start_matches") or [])
        _check(selection.get("policy") == "start_seed" and len(start_matches) == 1,
               "round 1 did not offer exactly one exact work START candidate",
               {"selection": selection, "offered": cognition.last_offered})
        _check(str(first.get("selected_candidate_ref") or "") == str(start_matches[0]),
               "round 1 selected a candidate other than the exact work offer",
               {"selected_candidate_ref": first.get("selected_candidate_ref"),
                "selection": selection})
        offered_rows = list(cognition.last_offered.get("candidates") or [])
        chosen_row = next((row for row in offered_rows
                           if row.get("candidate_id") == start_matches[0]), None)
        _check(chosen_row is not None,
               "the selected candidate is not in the offered legal set",
               {"selection": selection, "offered": cognition.last_offered})
        seed_ref = _work_ref_for_seed(seed)
        _check(seed_ref in [str(ref) for ref in (chosen_row.get("target_refs") or [])],
               "the selected candidate does not name the seed by whole ref",
               {"seed_ref": seed_ref, "candidate": chosen_row})
        _check(not _is_closeout_candidate(chosen_row),
               "round 1 selected a closeout wrapper", chosen_row)
        _check([row.get("candidate_id") for row in _work_start_matches(offered_rows, seed)]
               == start_matches,
               "the recorded selection disagrees with the offered legal set",
               {"selection": selection, "offered": cognition.last_offered})

        activity_ref = str(first.get("activity_ref") or "")
        _check(bool(activity_ref), "round 1 created no Activity", first)
        seed_after_first = _version(port, seed)
        _check(seed_after_first == seed_before + 1,
               "round 1 did not commit a real seed version",
               {"before": seed_before, "after": seed_after_first, "round": first})

        rows = memory.port.recent(limit=10)
        row = next((item for item in rows
                    if item.activity_ref == activity_ref
                    and seed in str(item.artifact_ref or "")), None)
        _check(row is not None, "the real owner result never became an admitted experience",
               {"activity_ref": activity_ref,
                "rows": [item.to_dict() for item in rows],
                "handoff": first.get("experience_handoff"),
                "handoff_error": first.get("experience_handoff_error")})

        # -- pure history: same baseline, read-only -------------------------------
        view = cycle.read_continuity()
        _check(view.get("past_state") == "OK", "continuity did not read the admitted past", view)
        step = view.get("next_step") or {}
        _check(step.get("reason_code") == "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT",
               "continuity did not name the owner-confirmed object", step)
        _check(cycle.continuity_continue_object() == seed,
               "continuity named a different object", {"object": cycle.continuity_continue_object(),
                                                        "seed": seed})
        point_hist, sel_hist = cycle._read_resume_point()
        _check(point_hist is not None and point_hist.artifact_id == seed,
               "history did not select the owner-confirmed seed", sel_hist)
        _check(sel_hist.get("selected_by") == "CONTINUITY_HISTORY",
               "history selection was not recorded", sel_hist)
        _check(sel_hist.get("history_state") == "OK",
               "history version did not agree with the owner head", sel_hist)

        # -- create the other work and rebind the round to it ----------------------
        created = port.create_artifact(
            workspace_id=data["workspace_id"], title="另一件作品",
            content="另一件正文第一句。\n", grant_id=data["artifact_grant_id"],
            operation_key="past-candidate-smoke:create-a2")
        _check(getattr(created, "applied", False), "second artifact was not created",
               created.to_dict())
        second_row = next((row for row in port.list_artifacts()
                           if str(row.get("title") or "") == "另一件作品"), None)
        _check(second_row is not None, "second artifact is not in the owner list",
               port.list_artifacts())
        other = str(second_row["artifact_id"])
        other_before = _version(port, other)
        cycle.work_artifact_id = other

        # Without history on the same baseline, the round's own selection falls back to the bound
        # work; with history it was the admitted seed.  This is the pure history evidence.
        cycle.last_continuity = None
        cycle.continuity_source = None
        point_bound, sel_bound = cycle._read_resume_point()
        _check(point_bound is not None and point_bound.artifact_id == other,
               "without history the bound work was not selected", sel_bound)
        _check(sel_bound.get("selected_by") == "BOUND_WORK",
               "without history the selection was not BOUND_WORK", sel_bound)

        # -- the fix: the running Activity's own target wins ----------------------
        cognition.policy = "continue_current"
        second = cycle.evaluate_once(trigger_ref="evt:past-smoke:2", act=True)
        _check(second.get("outcome") == "ACTED", "round 2 did not act", second)
        _check(second.get("selected_candidate_kind") in (None, "CONTINUE_CURRENT")
               or second.get("decision", "") in ("", "CONTINUE"),
               "round 2 did not continue the running Activity", second)
        selection2 = cognition.last_selection
        continue_matches = list(selection2.get("work_continue_matches") or [])
        _check(len(continue_matches) == 1
               and str(second.get("selected_candidate_ref") or "") == str(continue_matches[0]),
               "round 2 did not continue the exact CURRENT_ACTIVITY offer",
               {"selected_candidate_ref": second.get("selected_candidate_ref"),
                "selection": selection2, "offered": cognition.last_offered})
        seed_after_second = _version(port, seed)
        other_after = _version(port, other)
        _check(seed_after_second == seed_after_first + 1,
               "the running Activity's own work did not gain a version",
               {"seed_before": seed_after_first, "seed_after": seed_after_second,
                "offered": cognition.last_offered, "round": second})
        _check(other_after == other_before,
               "the bound work was written even though the Activity named the seed",
               {"other_before": other_before, "other_after": other_after,
                "offered": cognition.last_offered, "round": second})
        _check(str(second.get("foreground_work_ref") or "") == seed,
               "the round did not report the Activity's own work target",
               {"foreground_work_ref": second.get("foreground_work_ref"), "seed": seed})
        _check(str(second.get("step_work_target") or "") == "artifact:" + seed,
               "the round report and the executed step disagree",
               {"step_work_target": second.get("step_work_target"),
                "seed": seed, "round": second})

        print("SMOKE_OK")
        print(json.dumps({
            "root": str(RUN),
            "activity_ref": activity_ref,
            "seed": seed,
            "other": other,
            "seed_versions": [seed_before, seed_after_first, seed_after_second],
            "other_versions": [other_before, other_after],
            "history_selection": {"selected_by": sel_hist.get("selected_by"),
                                  "history_state": sel_hist.get("history_state"),
                                  "next_step": step.get("reason_code")},
            "no_history_selection": {"selected_by": sel_bound.get("selected_by")},
            "round2": {"decision": second.get("decision"),
                       "step_work_target": second.get("step_work_target"),
                       "foreground_work_ref": second.get("foreground_work_ref"),
                       "artifact_selection": second.get("artifact_selection")},
            "model_calls": cognition.calls,
        }, ensure_ascii=False, default=str, indent=1))
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
