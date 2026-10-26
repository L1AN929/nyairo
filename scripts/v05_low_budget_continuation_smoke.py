#!/usr/bin/env python3
"""v0.5 EP17 零模型真实 owner 冒烟：低预算时正式窗口怎样诚实地拒绝与接续。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发、0 旧数据。复用
``result_method_behavior_smoke`` 的隔离装配与 ``v05_result_sharing_continuation_smoke``
的正式轮助手，并在 **创建时** 用合法的小额度把两个层都接上：

* ``LifeRequestBudget``（真实 UTC 日上限 + 持久 run 窗口上限，每次 HTTP 尝试前预留，
  没有退款 API）；
* ``RoundBudget``（回合/认知/动作计数，认领请求账本的 ``utc_day_calls``）；
* 可选 ``CappedRequestBudget + ExperimentRequestCap``（实验硬上限，跨重开不回血）。

Decision 与内容回答是脚本替身（明示，经原有 AG-1 原始 JSON 校验），内容请求走正式
``BudgetedModelClient``，cognition 请求走正式 ``BudgetedCognitionAdapter``。真实件：
Supply owner、M0 Memory、ProductionLifeRuntime、AG-0/AG-1/AG-2/AR-0、正式 runner。

四个隔离根各验一块，账本创建后不改额、不清账、不换 run、不直改 ledger：

``a`` 日上限 2 / 窗口 2 / 认知 2 / 实验上限 2
    1. 低但足够（正好 2 次）：Decision + 内容各一次真实预留，owner 写出一版；
       写成功不宣称整件 Activity 完成。
    2. 剩余 0：下一正式窗口具名 ``COGNITION_BUDGET_EXHAUSTED`` 拒绝；provider /
       cognition / content 发送 0；版本、Activity、receipt、Memory 不虚增。
    3. 报告面：wake 是 ``BLOCKED`` / ``BUDGET_EXHAUSTED``（不是 QUIET/NO_ACTION），
       budget 视图与 requests 视图能看到真实用量；拒绝不是模型选择。
    4. 同根同进程重装配：账本用量、活动窗口、Activity/目标/停点保持；耗尽窗口不会
       因重开或业务时钟快进而续杯；坏账 fail-closed 不读成 0（只在最后注入故障）。

``b`` 日上限 5 / 窗口 5 / 认知 5
    NO_ACTION 只用一次认知、不发内容请求、不强迫耗掉余量；同一状态再开窗按原合同
    ``QUIET``（0 请求）。

``c`` 日上限 3 / 窗口 6 / 认知 3
    剩余 1：Decision 真的发出并提交，内容请求在真正预留处被
    ``REQUEST_BUDGET_UTC_DAY_LIMIT`` 拒绝；记录 Decision/Adoption/Action 各自真实
    阶段：已用请求不退、内容 0 次、无新版本/Action；下一窗口不自动重放。
    该轮的 ``llm_calls`` 是**模型决策轮**口径：Decision 请求真的发出（非
    ``RULE_RESOLVED``），所以是 1，而不是 0（没发）也不是 2（把被拒内容算进去）。
    之后只在测试内替换账本自己的 UTC 日读口（``finally`` 由 ``mock.patch`` 恢复），
    原 run 窗口仍有余额时，新 Decision 依法 CONTINUE 同一 Activity/同一作品：
    owner 恰好新增一版、恰好新增一个 Action，旧已采纳 Decision 与旧成功 Action
    都不重放，窗口累计 3 + 2 = 5/6，当日 2。

``d`` 日上限 2 / 窗口 6 / 认知 2
    日额度用尽后，用**测试内注入的账本 UTC 日读口**模拟日界（不改系统钟、不把业务
    快进当 UTC 预算）：同日 run 窗口仍保留 2 次余量，新 Decision 依法继续同一
    Activity/同一作品，owner 新增一版，旧成功不重放；窗口总额度不增加。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/v05_low_budget_continuation_smoke.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
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

#: 每轮自己的观察时刻；400 分钟越过既有 6 小时静默 TTL（这是夹具时钟，不是自然日）。
BASE_MOMENT = datetime.fromisoformat("2026-11-10T09:00:00+00:00")
STEP = 400
FIRST_TEXT = "第一段：预算内真正写出的一版。"
SECOND_TEXT = "第二段：跨过模拟日界后依法续写的下一版。"

CHECKS: list[tuple[str, bool, object]] = []
ROUNDS: list[dict] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-76s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def moment(minutes: int) -> datetime:
    return BASE_MOMENT + timedelta(minutes=minutes)


def stamp(minutes: int) -> str:
    return moment(minutes).isoformat()


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_id_for(name: str) -> str:
    return "run:v05-low-budget:%s" % name


# ---------------------------------------------------------------------------------------------
# 隔离装配：真实 owner + 真实 runtime + 正式 runner，创建时接上两层预算
# ---------------------------------------------------------------------------------------------


def open_chain(root, name: str, *, day_max: int, window_max: int, max_cog: int,
               experiment_cap: int | None = None, create_experiment_cap: bool = False,
               allow_new_window: bool = True, clock=None):
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_request_budget as lrb
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    import v05_restart_continuation_smoke as restart
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter
    import result_method_behavior_smoke as harness
    import v05_result_sharing_continuation_smoke as sharing

    root = Path(root)
    services = restart._start_services(root / "supply")
    runtime = None
    try:
        instance = root / "instance"
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        state_root = odl.life_state_root(instance)
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
            # 夹具自带的回合上限与认知上限：合法的小额度，不是产品默认。
            "CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY": "20",
            "CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY": str(int(max_cog)),
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))

        run_id = run_id_for(name)
        ledger = lrb.LifeRequestBudget(state_root, utc_day_max=int(day_max),
                                       run_window_max=int(window_max), run_id=run_id)
        ledger.ensure_window(allow_new=bool(allow_new_window))
        budget = ledger
        if experiment_cap is not None:
            from demo_business_clock import CappedRequestBudget, ExperimentRequestCap

            cap = ExperimentRequestCap(state_root, run_id=run_id,
                                       max_new_requests=int(experiment_cap))
            if create_experiment_cap:
                cap.create(baseline_run_window_calls=0)
            budget = CappedRequestBudget(ledger, cap)

        planner = sharing.PlanCognition()
        cognition = lrb.BudgetedCognitionAdapter(planner, budget)
        client = harness.ScriptedClient()
        guarded_client = lrb.BudgetedModelClient(client, budget)
        writer = ModelContinuationWriter(guarded_client, max_new_chars=300)
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
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=consumer)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer,
            requests=budget, enabled=True, clock=clock)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        if (assembled.get("practice") or {}).get("status") != "READY":
            raise SystemExit("practice workspace not READY: %r"
                             % (assembled.get("practice"),))
        return SimpleNamespace(
            root=root, name=name, services=services, runtime=runtime,
            cycle=assembled["cycle"], runner=assembled["runner"], planner=planner,
            cognition=cognition, client=client, guarded_client=guarded_client,
            budget=budget, ledger=ledger, data=data, env=env, port=port,
            memory_port=memory_port, evidence_db=evidence_db, controls_db=controls_db,
            state_root=state_root, run_id=run_id)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:  # noqa: BLE001 - printed, not hidden
                pass
        try:
            restart._stop_services(services)
        except Exception as exc:  # noqa: BLE001
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        raise


def close_chain(chain) -> dict:
    import v05_restart_continuation_smoke as restart

    return restart.close_chain(chain)


# ---------------------------------------------------------------------------------------------
# 安全的结构化读面（不打印提示词、正文、reasoning、密钥、环境变量）
# ---------------------------------------------------------------------------------------------


def run_window(chain, minutes: int, tag: str):
    record = chain.runner.run_due(act=True, now=moment(minutes))
    ROUNDS.append({
        "fixture": chain.name, "round": tag,
        "status": str(record.status), "class": str(record.status_class),
        "outcome": str(record.outcome or ""), "decision": str(record.decision or ""),
        "llm_calls": int(record.llm_calls or 0),
        "decision_route": str((record.extra or {}).get("decision_route") or ""),
        "action_ref": bool(record.action_id),
        "owner_version_created": (record.extra or {}).get("owner_version_created"),
        "detail": str(record.detail or "")[:160],
    })
    return record


def budget_view(chain) -> dict:
    return dict(chain.budget.view() or {})


def request_counts(chain) -> dict:
    view = budget_view(chain)
    return {"state": view.get("state"),
            "utc_day": view.get("utc_day"),
            "utc_day_calls": view.get("utc_day_calls"),
            "utc_day_max": view.get("utc_day_max"),
            "run_window_calls": view.get("run_window_calls"),
            "run_window_max": view.get("run_window_max"),
            "run_id": view.get("run_id")}


def ledger_token_kinds(chain) -> list[str]:
    try:
        data = json.loads(chain.ledger.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(row.get("kind") or "") for row in (data.get("tokens") or [])]


def work_state(chain, artifact_id: str) -> dict:
    point = chain.port.resume_point(artifact_id) if artifact_id else None
    if point is None:
        return {}
    return {"head": str(getattr(point, "head_version_id", "") or ""),
            "version": int(getattr(point, "version_number", 0) or 0),
            "lifecycle": str(getattr(point, "lifecycle", "") or ""),
            "_content": str(getattr(point, "content", "") or "")}


def public_work(state: dict) -> dict:
    return {key: value for key, value in state.items() if not key.startswith("_")}


def activity_state(chain) -> dict:
    state, _journal = chain.runtime.activity_read.read_canonical_state()
    fg = state.get("foreground_activity_ref")
    act = (state.get("activities") or {}).get(fg) or {}
    return {"revision": state.get("revision"), "foreground": fg,
            "status": act.get("status"), "kind": act.get("activity_kind"),
            "activity_id": act.get("activity_id"),
            "checkpoint_ref": act.get("checkpoint_ref")}


def decision_count(chain) -> int:
    try:
        data = json.loads(chain.runtime.decision_store.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(data.get("decisions_by_id") or {})


def decision_ids(chain) -> set[str]:
    try:
        data = json.loads(chain.runtime.decision_store.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(key) for key in (data.get("decisions_by_id") or {})}


def adopted_decision_refs(chain) -> list[str]:
    """The decisions the integration owner really adopted (from its own journal)."""

    refs: list[str] = []
    try:
        rows = chain.runtime.integration_read.read_journal() or []
    except Exception:  # noqa: BLE001 - a failed read is reported as an empty list
        return refs
    for row in rows:
        payload = row.get("payload") or {}
        if str(payload.get("event_type") or "") != "LIFE_INTEGRATION_ADOPTED":
            continue
        delta = row.get("state_delta") or {}
        integrations = ((delta.get("map_updates") or {}).get("integrations_by_id") or {})
        for integration in integrations.values():
            ref = str((integration or {}).get("decision_ref") or "")
            if ref:
                refs.append(ref)
    return refs


def action_count(chain) -> int:
    try:
        return len(chain.runtime.action_read.list_actions() or [])
    except Exception:  # noqa: BLE001
        return -1


def receipt_tuple(chain):
    receipt = getattr(chain.cycle, "last_receipt", None)
    if receipt is None:
        return None
    return (str(receipt.artifact_id), int(receipt.version_number or 0),
            str(receipt.status), str(receipt.version_id or ""))


def memory_rows(chain) -> int:
    path = Path(chain.evidence_db)
    if not path.exists():
        return 0
    try:
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error:
        return -1
    try:
        row = connection.execute("SELECT count(*) FROM evidence_events").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1
    finally:
        connection.close()


# ---------------------------------------------------------------------------------------------
# 正式轮：真实候选 + 脚本 Decision + 真实 owner 写入
# ---------------------------------------------------------------------------------------------


def start_candidate(chain, observed_at: str) -> str:
    options = chain.cycle.project_options()
    wanted = [row for row in options if not bool(row.get("review"))]
    if not wanted:
        raise SystemExit("no project option on offer: %r" % (options,))
    option_ref = str(wanted[0]["option_ref"])
    refs = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at).candidate_refs
    for ref in refs:
        payload = chain.runtime.candidate_read.get_candidate(str(ref)) or {}
        if str(payload.get("source_ref")) == option_ref:
            return str(ref)
    raise SystemExit("the project option never became a candidate")


def seed_first_write(chain, minutes: int) -> SimpleNamespace:
    """真实 owner 写一版：正式窗口 → Decision START → AG-1/AG-2/AR-0 → 内容一次。"""

    import result_method_behavior_smoke as harness

    material_id, revision = harness.import_material(chain)
    harness.write_question(chain, material_id, revision)
    chain.client.payload = {"kind": "append_paragraph", "text": FIRST_TEXT,
                            "self_note": "stub: write first version"}
    observed = stamp(minutes)
    candidate = start_candidate(chain, observed)
    chain.planner.plan = [{"script": {"decision": "START",
                                      "selected_candidate_id": candidate,
                                      "deferred_candidate_ids": []}}]
    wake = run_window(chain, minutes, "seed")
    created = dict(getattr(chain.cycle, "last_project_created", None) or {})
    artifact_id = str(created.get("artifact_id") or "")
    chain.cycle.work_artifact_id = artifact_id or chain.cycle.work_artifact_id
    return SimpleNamespace(wake=wake, artifact_id=artifact_id,
                           material_id=material_id, revision=revision)


def continue_round(chain, artifact_id: str, minutes: int, text: str, tag: str):
    import v05_result_sharing_continuation_smoke as sharing

    observed = stamp(minutes)
    candidate = sharing.continue_candidate(chain, artifact_id, observed_at=observed)
    chain.client.payload = {"kind": "append_paragraph", "text": text,
                            "self_note": "stub: continue the same work"}
    chain.planner.plan = [{"script": {"decision": "CONTINUE",
                                      "selected_candidate_id": candidate,
                                      "deferred_candidate_ids": []}}]
    wake = run_window(chain, minutes, tag)
    return wake, candidate


def quiet_round(chain, minutes: int, tag: str):
    chain.planner.plan = [{"script": {"decision": "NO_ACTION",
                                      "selected_candidate_id": None,
                                      "deferred_candidate_ids": [],
                                      "short_rationale": "stub: nothing to do"}}]
    return run_window(chain, minutes, tag)


# ---------------------------------------------------------------------------------------------
# a：低但足够 → 剩余 0 拒绝 → 报告面 → 同根重装/坏账
# ---------------------------------------------------------------------------------------------


def fixture_a() -> None:
    import life_request_budget as lrb
    import result_method_behavior_smoke as harness

    print("\n== fixture a: exactly two requests, then an honest refusal ==")
    root = harness._fresh_root("v05-low-budget-a")
    import v05_restart_continuation_smoke as restart

    restart.prepare_fixture(root)
    chain = open_chain(root, "a", day_max=2, window_max=2, max_cog=2,
                       experiment_cap=2, create_experiment_cap=True)
    reopened = None
    try:
        seed = seed_first_write(chain, 0)
        artifact = seed.artifact_id
        wake = seed.wake
        ledger = request_counts(chain)
        work = work_state(chain, artifact)
        activity = activity_state(chain)

        # -- 场景 3：低但足够（正好 2 次），owner 真的写出一版 -------------------------
        check("a formal window really wrote one version through the owner",
              wake.status == "ACTED" and wake.outcome == "ACTED"
              and (wake.extra or {}).get("owner_version_created") is True
              and bool(artifact) and work.get("version", 0) >= 2,
              {"wake": wake.to_dict(), "artifact": artifact, "work": public_work(work)})
        check("the two reservations are one decision and one content request",
              ledger["utc_day_calls"] == 2 and ledger["run_window_calls"] == 2
              and ledger_token_kinds(chain) == ["decision", "content"]
              and [row.get("kind") for row in chain.cognition.attempts] == ["decision"]
              and [row.get("kind") for row in chain.guarded_client.attempts] == ["content"],
              {"view": ledger, "tokens": ledger_token_kinds(chain),
               "cognition": chain.cognition.attempts,
               "content": chain.guarded_client.attempts})
        check("a written step does not announce the whole activity as completed",
              wake.outcome == "ACTED" and activity.get("status") == "ACTIVE",
              {"outcome": wake.outcome, "activity": activity})

        # -- 场景 1：剩余 0，下一正式窗口具名拒绝，零发送、零虚增 ----------------------
        before = {
            "planner": chain.planner.call_count, "content": len(chain.client.prompts),
            "ledger": request_counts(chain), "tokens": ledger_token_kinds(chain),
            "work": work_state(chain, artifact), "activity": activity_state(chain),
            "receipt": receipt_tuple(chain), "memory": memory_rows(chain),
            "decisions": decision_count(chain), "actions": action_count(chain),
        }
        refused = run_window(chain, STEP, "exhausted")
        after = {
            "planner": chain.planner.call_count, "content": len(chain.client.prompts),
            "ledger": request_counts(chain), "tokens": ledger_token_kinds(chain),
            "work": work_state(chain, artifact), "activity": activity_state(chain),
            "receipt": receipt_tuple(chain), "memory": memory_rows(chain),
            "decisions": decision_count(chain), "actions": action_count(chain),
        }
        check("with nothing left the formal window refuses by name (no request at all)",
              refused.status == "BLOCKED" and refused.status_class == "blocked"
              and refused.outcome is None and refused.llm_calls == 0
              and "COGNITION_BUDGET_EXHAUSTED" in str(refused.detail or ""),
              {"status": refused.status, "class": refused.status_class,
               "outcome": refused.outcome, "detail": refused.detail})
        check("...and nothing was sent: cognition and content call counts are unchanged",
              after["planner"] == before["planner"]
              and after["content"] == before["content"],
              {"planner": [before["planner"], after["planner"]],
               "content": [before["content"], after["content"]]})
        check("...the ledger, version, Activity, receipt and Memory do not grow",
              after["ledger"] == before["ledger"] and after["tokens"] == before["tokens"]
              and public_work(after["work"]) == public_work(before["work"])
              and after["activity"] == before["activity"]
              and after["receipt"] == before["receipt"]
              and after["memory"] == before["memory"]
              and after["decisions"] == before["decisions"]
              and after["actions"] == before["actions"],
              {"before": {k: (public_work(v) if k == "work" else v)
                          for k, v in before.items()},
               "after": {k: (public_work(v) if k == "work" else v)
                         for k, v in after.items()}})

        # -- 场景 6：既有状态/摘要/视图能把“没发”说清楚 --------------------------------
        status = chain.runner.status()
        cycle_budget = status.get("budget") or {}
        requests_view = cycle_budget.get("requests") or {}
        check("the refusal is reported as a technical block, never as her quiet choice",
              refused.status not in {"QUIET", "NO_ACTION", "ACTED", "ALREADY_DONE"}
              and refused.status_class == "blocked"
              and (refused.extra or {}).get("owner_version_created") is not True,
              {"status": refused.status, "class": refused.status_class,
               "owner_version_created": (refused.extra or {}).get("owner_version_created")})
        check("the existing budget views expose the real use, the ceilings and no refunds",
              requests_view.get("utc_day_calls") == 2
              and requests_view.get("utc_day_max") == 2
              and requests_view.get("run_window_calls") == 2
              and requests_view.get("run_window_max") == 2
              and requests_view.get("state") == "ok"
              and "no release" in str(requests_view.get("refund_policy") or "")
              and chain.cycle.budget.cognition_remaining() == 0
              and chain.cycle.budget.may_start_wake()
              == (False, "COGNITION_BUDGET_EXHAUSTED"),
              {"requests": requests_view,
               "remaining": chain.cycle.budget.cognition_remaining(),
               "may_start_wake": list(chain.cycle.budget.may_start_wake())})

        # -- 场景 4：同根同进程重装配，用量/停点保持；耗尽窗口不续杯 --------------------
        snapshot = {"ledger": request_counts(chain), "tokens": ledger_token_kinds(chain),
                    "work": public_work(work_state(chain, artifact)),
                    "activity": activity_state(chain), "memory": memory_rows(chain),
                    "experiment": (budget_view(chain).get("experiment_cap") or {})}
        closed = close_chain(chain)
        chain = None
        check("the owner and runtime closed in the production order",
              closed.get("closed") is True and closed.get("service_stopped") is True,
              closed)
        reopened = open_chain(root, "a", day_max=2, window_max=2, max_cog=2,
                              experiment_cap=2, create_experiment_cap=False,
                              allow_new_window=False,
                              clock=lambda: moment(24 * 60))
        reopened.budget.ensure_window(allow_new=False)
        ledger_now = request_counts(reopened)
        experiment_now = budget_view(reopened).get("experiment_cap") or {}
        check("re-opening the same root keeps the ledger and the run window",
              ledger_now == snapshot["ledger"]
              and ledger_token_kinds(reopened) == snapshot["tokens"],
              {"before": snapshot["ledger"], "after": ledger_now})
        check("...the activity, its target, the work stop point and Memory are kept",
              activity_state(reopened) == snapshot["activity"]
              and public_work(work_state(reopened, artifact)) == snapshot["work"]
              and memory_rows(reopened) == snapshot["memory"],
              {"activity": activity_state(reopened)})
        check("the exhausted experiment cap is not refunded by a re-open",
              experiment_now.get("used") == 2 and experiment_now.get("remaining") == 0
              and experiment_now.get("allowed") is False,
              experiment_now)
        new_window_refused = False
        try:
            reopened.ledger.__class__(
                reopened.state_root, utc_day_max=2, run_window_max=2,
                run_id="run:v05-low-budget:a-other").ensure_window(allow_new=False)
        except lrb.RequestBudgetError as exc:
            new_window_refused = exc.reason_code == lrb.REASON_RUN_WINDOW_NOT_AUTHORIZED
        check("a plain restart cannot mint a fresh run window", new_window_refused)
        fast = run_window(reopened, 2 * STEP, "reopen-fast-clock")
        check("a business-clock fast-forward does not reopen the exhausted budget",
              fast.status == "BLOCKED" and fast.llm_calls == 0
              and request_counts(reopened) == snapshot["ledger"]
              and lrb._utc_day() == utc_today(),
              {"wake": {"status": fast.status, "detail": fast.detail},
               "ledger": request_counts(reopened), "real_utc_day": utc_today()})

        # -- 场景 4（负控）：坏账 fail-closed，不读成 0 --------------------------------
        before_bad = {"planner": reopened.planner.call_count,
                      "content": len(reopened.client.prompts),
                      "work": public_work(work_state(reopened, artifact)),
                      "activity": activity_state(reopened)}
        reopened.ledger.path.write_text("{not json", encoding="utf-8")
        bad_view = reopened.ledger.view()
        bad_wake = run_window(reopened, 3 * STEP, "corrupt-ledger")
        after_bad = {"planner": reopened.planner.call_count,
                     "content": len(reopened.client.prompts),
                     "work": public_work(work_state(reopened, artifact)),
                     "activity": activity_state(reopened)}
        check("a corrupt ledger fails closed instead of reading as zero",
              bad_view.get("state") == "corrupt" and bad_view.get("utc_day_calls") is None
              and reopened.cycle.budget.cognition_used_today() == 2
              and reopened.cycle.budget.cognition_remaining() == 0,
              {"state": bad_view.get("state"), "calls": bad_view.get("utc_day_calls"),
               "used": reopened.cycle.budget.cognition_used_today()})
        check("...the window refuses without a request and nothing changes",
              bad_wake.status == "BLOCKED" and bad_wake.llm_calls == 0
              and "COGNITION_BUDGET_EXHAUSTED" in str(bad_wake.detail or "")
              and after_bad["planner"] == before_bad["planner"]
              and after_bad["content"] == before_bad["content"]
              and after_bad["work"] == before_bad["work"]
              and after_bad["activity"] == before_bad["activity"],
              {"wake": {"status": bad_wake.status, "detail": bad_wake.detail},
               "before": before_bad, "after": after_bad})
    finally:
        if chain is not None:
            close_chain(chain)
        if reopened is not None:
            close_chain(reopened)


# ---------------------------------------------------------------------------------------------
# b：NO_ACTION 不需要内容、不强迫耗余量；同状态再开窗是 QUIET
# ---------------------------------------------------------------------------------------------


def fixture_b() -> None:
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as restart

    print("\n== fixture b: NO_ACTION costs one cognition, then the same state is QUIET ==")
    root = harness._fresh_root("v05-low-budget-b")
    restart.prepare_fixture(root)
    chain = open_chain(root, "b", day_max=5, window_max=5, max_cog=5)
    try:
        seed = seed_first_write(chain, 0)
        artifact = seed.artifact_id
        work_before = work_state(chain, artifact)
        prompts_before = len(chain.client.prompts)
        decisions_before = decision_count(chain)

        quiet = quiet_round(chain, STEP, "no-action")
        ledger = request_counts(chain)
        check("a NO_ACTION round really happened and committed a Decision",
              quiet.status == "NO_ACTION" and quiet.status_class == "choice"
              and decision_count(chain) == decisions_before + 1,
              {"status": quiet.status, "class": quiet.status_class,
               "decisions": decision_count(chain)})
        check("...it spends exactly one cognition reservation and no content request",
              ledger["utc_day_calls"] == 3
              and ledger_token_kinds(chain) == ["decision", "content", "decision"]
              and len(chain.client.prompts) == prompts_before
              and chain.guarded_client.attempts
              and [row.get("kind") for row in chain.guarded_client.attempts] == ["content"],
              {"view": ledger, "tokens": ledger_token_kinds(chain),
               "content_prompts": len(chain.client.prompts)})
        check("...and it does not write a version or force the remaining quota",
              public_work(work_state(chain, artifact)) == public_work(work_before)
              and chain.cycle.budget.cognition_remaining() == 2,
              {"work": public_work(work_state(chain, artifact)),
               "remaining": chain.cycle.budget.cognition_remaining()})

        same_state = run_window(chain, STEP + 121, "quiet-same-state")
        check("the same state in a due window is QUIET and spends nothing",
              same_state.status == "QUIET" and same_state.status_class == "silence"
              and same_state.llm_calls == 0
              and request_counts(chain) == ledger
              and len(chain.client.prompts) == prompts_before
              and public_work(work_state(chain, artifact)) == public_work(work_before),
              {"status": same_state.status, "ledger": request_counts(chain)})
    finally:
        close_chain(chain)


# ---------------------------------------------------------------------------------------------
# c：剩余 1 —— Decision 已付费提交，内容请求在预留处被拒
# ---------------------------------------------------------------------------------------------


def fixture_c() -> None:
    import life_request_budget as lrb
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as restart

    print("\n== fixture c: one left -> decision paid, content refused at reservation ==")
    root = harness._fresh_root("v05-low-budget-c")
    restart.prepare_fixture(root)
    chain = open_chain(root, "c", day_max=3, window_max=6, max_cog=3)
    try:
        seed = seed_first_write(chain, 0)
        artifact = seed.artifact_id
        seed_action = seed.wake.action_id
        work_before = work_state(chain, artifact)
        receipt_before = receipt_tuple(chain)
        receipt_object = getattr(chain.cycle, "last_receipt", None)
        planner_before = chain.planner.call_count
        prompts_before = len(chain.client.prompts)
        decisions_before = decision_ids(chain)
        adopted_before = adopted_decision_refs(chain)
        actions_before = action_count(chain)

        wake, candidate = continue_round(chain, artifact, STEP, SECOND_TEXT, "last-request")
        ledger = request_counts(chain)
        decisions_after = decision_ids(chain)
        adopted_after = adopted_decision_refs(chain)
        new_decisions = decisions_after - decisions_before
        check("the content request is refused at its own reservation, by name",
              wake.status == "BLOCKED" and wake.outcome == "BUDGET_EXHAUSTED"
              and "REQUEST_BUDGET_UTC_DAY_LIMIT" in str(wake.detail or "")
              and ledger["utc_day_calls"] == 3 and ledger["run_window_calls"] == 3,
              {"wake": {"status": wake.status, "outcome": wake.outcome,
                        "detail": wake.detail}, "view": ledger})
        check("the decision request was really sent (and is not refunded)",
              bool(candidate) and chain.planner.call_count == planner_before + 1
              and ledger_token_kinds(chain) == ["decision", "content", "decision"],
              {"candidate": bool(candidate), "calls": chain.planner.call_count,
               "tokens": ledger_token_kinds(chain)})
        check("the content request was never sent",
              len(chain.client.prompts) == prompts_before
              and [row.get("kind") for row in chain.guarded_client.attempts] == ["content"],
              {"prompts": len(chain.client.prompts),
               "content_attempts": chain.guarded_client.attempts})
        # 计数口径：wake.llm_calls 是"模型决策轮/尝试"，不是 HTTP 次数、不是当日累计。
        # 本轮 Decision 请求真的发出（planner 调用 +1、账本多一枚 decision token），
        # 内容请求在预留处被拒且 0 次发送，所以精确值是 1：0 会把已付费决策抹掉，
        # 2 会把被拒内容混进来，3 会把当日累计当本轮。
        check("a round that paid a Decision and had its content refused counts one model round",
              wake.llm_calls == 1
              and chain.planner.call_count == planner_before + 1
              and len(chain.client.prompts) == prompts_before
              and ledger_token_kinds(chain) == ["decision", "content", "decision"],
              {"llm_calls": wake.llm_calls,
               "planner_calls": chain.planner.call_count,
               "content_prompts": len(chain.client.prompts),
               "tokens": ledger_token_kinds(chain)})
        check("the committed Decision was adopted by the owner; still no Action ran",
              len(new_decisions) == 1
              and len(adopted_after) == len(adopted_before) + 1
              and new_decisions.issubset(set(adopted_after))
              and action_count(chain) == actions_before
              and getattr(chain.cycle, "last_receipt", None) is receipt_object,
              {"new_decision": sorted(new_decisions),
               "adopted_delta": len(adopted_after) - len(adopted_before),
               "actions": [actions_before, action_count(chain)],
               "receipt_changed": receipt_tuple(chain) != receipt_before})
        check("no version, no receipt and no unknown effect is fabricated",
              public_work(work_state(chain, artifact)) == public_work(work_before)
              and receipt_tuple(chain) == receipt_before
              and (wake.extra or {}).get("owner_version_created") is False
              and wake.action_id is None
              and chain.runner.pending_unknown_actions() == [],
              {"work": public_work(work_state(chain, artifact)),
               "action_id": wake.action_id,
               "owner_version_created": (wake.extra or {}).get("owner_version_created")})
        retry = run_window(chain, 2 * STEP, "after-content-refusal")
        check("the next window does not auto-replay the refused step",
              retry.status == "BLOCKED" and retry.llm_calls == 0
              and chain.planner.call_count == planner_before + 1
              and len(chain.client.prompts) == prompts_before
              and request_counts(chain) == ledger
              and public_work(work_state(chain, artifact)) == public_work(work_before),
              {"wake": {"status": retry.status, "detail": retry.detail},
               "calls": chain.planner.call_count, "ledger": request_counts(chain)})

        # 读面：账本里真实的三次预留与拒绝原因并存，既有字段足够定位"发后失败"。
        # 放在日界注入之前读，避免把新一天的 2 次误当断步时那一轮的用量。
        status = chain.runner.status()
        requests_view = (status.get("budget") or {}).get("requests") or {}
        check("existing fields show the paid decision next to the refused content",
              requests_view.get("utc_day_calls") == 3
              and requests_view.get("state") == "ok"
              and "no release" in str(requests_view.get("refund_policy") or "")
              and lrb.REASON_UTC_DAY_LIMIT in str(wake.detail or ""),
              {"requests": requests_view, "detail": wake.detail})

        # -- 断步后续：账本日界（测试内读口注入）后，原 run 仍有窗口余额 -------------
        # 只在测试内替换账本自己的 ``_utc_day`` 读口模拟下一天，``mock.patch`` 退出即恢复；
        # 这不是系统钟跨日，也不是业务时钟快进，更不是新的公开 clock 形参。
        old_decision_ref = sorted(new_decisions)[0] if len(new_decisions) == 1 else ""
        real_day = lrb._utc_day()
        simulated_day = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        work_before_roll = work_state(chain, artifact)
        activity_before_roll = activity_state(chain)
        actions_before_roll = action_count(chain)
        decisions_before_roll = decision_ids(chain)
        adopted_before_roll = adopted_decision_refs(chain)
        planned_before_roll = chain.planner.call_count
        content_before_roll = len(chain.client.prompts)

        def simulated_utc_day(_moment=None) -> str:
            return simulated_day

        with mock.patch.object(lrb, "_utc_day", simulated_utc_day):
            rolled = request_counts(chain)
            check("the day boundary clears only the day: the paid decision still counts",
                  rolled["utc_day"] == simulated_day and rolled["utc_day_calls"] == 0
                  and rolled["run_window_calls"] == 3 and rolled["run_window_max"] == 6,
                  {"real_day": real_day, "view": rolled})
            follow_wake, follow_candidate = continue_round(
                chain, artifact, 3 * STEP, SECOND_TEXT, "after-refusal-day-roll")
            follow_work = work_state(chain, artifact)
            follow = request_counts(chain)
            follow_decisions = decision_ids(chain)
            follow_adopted = adopted_decision_refs(chain)
            follow_actions = action_count(chain)
            follow_new = follow_decisions - decisions_before_roll
            check("a later Decision legally continues the same Activity and the same work",
                  follow_wake.status == "ACTED" and follow_wake.outcome == "ACTED"
                  and bool(follow_candidate)
                  and follow_wake.activity_ref == activity_before_roll.get("foreground")
                  and str(chain.cycle.work_artifact_id) == artifact,
                  {"wake": {"status": follow_wake.status, "outcome": follow_wake.outcome,
                            "activity_ref": follow_wake.activity_ref},
                   "expected_activity": activity_before_roll.get("foreground")})
            check("the owner wrote exactly one new version; the refused step was not replayed",
                  follow_work.get("version") == work_before_roll.get("version", 0) + 1
                  and follow_work.get("head") != work_before_roll.get("head")
                  and SECOND_TEXT in str(follow_work.get("_content") or "")
                  and follow_actions == actions_before_roll + 1
                  and follow_wake.action_id
                  and follow_wake.action_id != seed_action,
                  {"versions": [work_before_roll.get("version"), follow_work.get("version")],
                   "head_changed": follow_work.get("head") != work_before_roll.get("head"),
                   "actions": [actions_before_roll, follow_actions],
                   "new_action": bool(follow_wake.action_id
                                      and follow_wake.action_id != seed_action)})
            check("the previously adopted Decision is not re-adopted or replayed",
                  len(follow_new) == 1
                  and len(follow_adopted) == len(adopted_before_roll) + 1
                  and follow_new.issubset(set(follow_adopted))
                  and bool(old_decision_ref)
                  and follow_adopted.count(old_decision_ref) == 1,
                  {"new": sorted(follow_new), "old_decision": old_decision_ref,
                   "old_decision_occurrences": follow_adopted.count(old_decision_ref),
                   "adopted_delta": len(follow_adopted) - len(adopted_before_roll)})
            check("the run keeps its total (3 + 2 of 6); only the new day's two calls count",
                  follow["utc_day"] == simulated_day and follow["utc_day_calls"] == 2
                  and follow["run_window_calls"] == 5 and follow["run_window_max"] == 6
                  and follow["run_id"] == chain.run_id
                  and chain.planner.call_count == planned_before_roll + 1
                  and len(chain.client.prompts) == content_before_roll + 1
                  and [row.get("kind") for row in chain.guarded_client.attempts]
                  == ["content", "content"]
                  and follow_wake.llm_calls == 1,
                  {"view": follow, "llm_calls": follow_wake.llm_calls,
                   "tokens": ledger_token_kinds(chain)})

        now_view = request_counts(chain)
        check("after the injection ends the real UTC clock is back and the day is monotonic",
              lrb._utc_day() == utc_today() and real_day == utc_today()
              and now_view["utc_day"] == simulated_day
              and now_view["utc_day_calls"] == 2
              and now_view["run_window_calls"] == 5,
              {"real_day": lrb._utc_day(), "view": now_view})
    finally:
        close_chain(chain)


# ---------------------------------------------------------------------------------------------
# d：真实 UTC 日滚动（账本日读口注入）＋ run 窗口余量 → 同一 Activity 续写
# ---------------------------------------------------------------------------------------------


def fixture_d() -> None:
    import life_request_budget as lrb
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as restart

    print("\n== fixture d: the UTC day rolls, the run window keeps its spent count ==")
    root = harness._fresh_root("v05-low-budget-d")
    restart.prepare_fixture(root)
    chain = open_chain(root, "d", day_max=2, window_max=6, max_cog=2)
    try:
        seed = seed_first_write(chain, 0)
        artifact = seed.artifact_id
        first_wake = seed.wake
        first_work = work_state(chain, artifact)
        first_activity = activity_state(chain)
        first_action = first_wake.action_id

        exhausted = run_window(chain, STEP, "day-spent")
        check("after two requests the UTC day is spent and the window refuses",
              exhausted.status == "BLOCKED"
              and "COGNITION_BUDGET_EXHAUSTED" in str(exhausted.detail or "")
              and request_counts(chain)["utc_day_calls"] == 2
              and request_counts(chain)["run_window_calls"] == 2,
              {"wake": {"status": exhausted.status, "detail": exhausted.detail},
               "view": request_counts(chain)})

        real_day = lrb._utc_day()
        simulated_day = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        def simulated_utc_day(_moment=None) -> str:
            return simulated_day

        # 账本没有公开的 clock 形参（``reserve(now=...)`` 只写 token 时间戳，不改变日）；
        # 这里只替换账本自己的真实 UTC 日读口来模拟日界，不改系统钟，也不改业务时钟。
        with mock.patch.object(lrb, "_utc_day", simulated_utc_day):
            rolled = request_counts(chain)
            check("the simulated day boundary keeps the run window and clears only the day",
                  rolled["utc_day"] == simulated_day and rolled["utc_day_calls"] == 0
                  and rolled["run_window_calls"] == 2 and rolled["run_window_max"] == 6,
                  {"real_day": real_day, "view": rolled})
            wake, candidate = continue_round(chain, artifact, 2 * STEP, SECOND_TEXT,
                                             "after-day-roll")
            after = request_counts(chain)
            work_after = work_state(chain, artifact)
            check("a new Decision legally continues the same Activity and the same work",
                  wake.status == "ACTED" and wake.outcome == "ACTED"
                  and bool(candidate) and wake.activity_ref
                  and wake.activity_ref == first_activity.get("foreground")
                  and str(chain.cycle.work_artifact_id) == artifact,
                  {"wake": {"status": wake.status, "outcome": wake.outcome,
                            "activity_ref": wake.activity_ref},
                   "expected_activity": first_activity.get("foreground")})
            check("the owner wrote exactly one new version; the old success was not replayed",
                  work_after.get("version") == first_work.get("version", 0) + 1
                  and work_after.get("head") != first_work.get("head")
                  and SECOND_TEXT in str(work_after.get("_content") or "")
                  and wake.action_id and wake.action_id != first_action,
                  {"versions": [first_work.get("version"), work_after.get("version")],
                   "head_changed": work_after.get("head") != first_work.get("head"),
                   "new_action": bool(wake.action_id and wake.action_id != first_action)})
            check("the run window keeps the total (2 + 2 of 6); no new allowance is minted",
                  after["utc_day_calls"] == 2 and after["run_window_calls"] == 4
                  and after["run_window_max"] == 6
                  and [row.get("kind") for row in chain.guarded_client.attempts]
                  == ["content", "content"]
                  and after["run_id"] == chain.run_id,
                  {"view": after, "tokens": ledger_token_kinds(chain)})

        now_view = request_counts(chain)
        check("after the injection ends the real UTC clock is back and the day is monotonic",
              lrb._utc_day() == utc_today()
              and now_view["utc_day"] == simulated_day
              and now_view["utc_day_calls"] == 2
              and now_view["run_window_calls"] == 4,
              {"real_day": lrb._utc_day(), "view": now_view})
    finally:
        close_chain(chain)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0
    fixture_a()
    fixture_b()
    fixture_c()
    fixture_d()
    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\nROUNDS (structural metadata only)")
    for entry in ROUNDS:
        print(json.dumps(entry, ensure_ascii=False, default=str))
    print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
