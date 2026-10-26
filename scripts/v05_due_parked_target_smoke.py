#!/usr/bin/env python3
"""v0.5 零模型真 owner 冒烟：到期「稍后」事项指向 WAITING/PAUSED 的同一个作品。

真实件：Supply owner（canonical db + Unix socket）、ProductionLifeRuntime（lease）、
正式 ``AutonomousLifeRunner.run_due`` → AG-1 原始 JSON → AG-2/LR-2/LR-4、`FutureItems`
真实持久存储、AG-0 正式候选源。替身只有 Decision 回答（脚本 raw JSON）与内容回答
（StubWriter）。0 产品模型、0 外网、不改任何旧实例。

三个隔离根，各自跑一条正式链：

1. PAUSED：到期窗口先把候选**展示**出来（目标此刻 ACTIVE），Decision 真实 PAUSE 同一作品；
   下一窗口目标 PAUSED 且无恢复资格 —— 候选必须被撤回、不得出现新 START/第二个 Activity。
2. WAITING：到期窗口真实 WAIT，同一时刻 owner 的 TIME_AT 到期；展示过的旧候选同样撤回，
   回来的路只有 LR-4 OPEN + 正式 RESUME（同一 Activity，资格恰好消费一次）。
3. 正控：目标作品仍 ACTIVE、没有挂起活动时，到期项照常供应、正式 START 并由 owner 写一版
   ——过滤只按目标是否有 PAUSED/WAITING 活动生效，不按 origin 字符串或整类事项误封。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/v05_due_parked_target_smoke.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import v05_due_reconsideration_smoke as driver  # noqa: E402
from test_v05_due_parked_target import ParkedCognition  # noqa: E402

CHECKS: list[tuple[str, bool, object]] = []

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
    print("CHECK %-74s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(_redact(detail), ensure_ascii=False, default=str)[:900])


def window(chain, minute: int, policy: str = "no_action", payload=None) -> dict:
    return driver.window(chain, driver.at(minute), policy, payload)


def activity_on(chain, artifact_id: str) -> list:
    return [row for row in chain.cycle.open_activities()
            if artifact_id in [str(ref) for ref in
                               (chain.cycle._activity_target_refs(row) or [])]]


def propose_on_artifact(chain, artifact_id: str, minute: int, delay: int) -> str:
    record = window(chain, minute, "no_action_propose",
                    {"title": "回头把这一段再改一遍", "in_minutes": delay,
                     "source_refs": ["artifact:%s" % artifact_id]})
    check("proposal is committed by real AG-1", record.get("decision") == "NO_ACTION", record)
    items = [row for row in driver.open_items(chain)
             if "artifact:%s" % artifact_id in (row.get("source_refs") or [])]
    check("the later item is stored with its artifact target", len(items) == 1, items)
    return str(items[0].get("item_ref") or "") if items else ""


def shown_candidate(chain, cognition, ref: str) -> dict:
    return next((dict(row) for row in (cognition.last_candidates or [])
                 if str(row.get("source_ref") or "") == ref), {})


# ---------------------------------------------------------------------------------------------
# 场景 1：PAUSED
# ---------------------------------------------------------------------------------------------


def scenario_paused(chain, cognition) -> dict:
    out: dict = {"phase": "paused"}
    seed = str(chain.data["artifact_id"])
    started = window(chain, 0, "start_seed")
    activity_ref = str(started.get("activity_ref") or "")
    check("1.1 official START really writes one version",
          started.get("outcome") == "ACTED", started)
    item_ref = propose_on_artifact(chain, seed, 1, 4)
    out["item_ref"] = item_ref
    head_before = driver.head_version(chain, seed)
    versions_before = driver.version_count(chain, seed)

    display = window(chain, 5, "pause_current")
    shown = shown_candidate(chain, cognition, item_ref)
    check("1.2 the due item is displayed while the target is still ACTIVE",
          bool(shown) and item_ref in driver.extra_items(chain), shown)
    check("1.3 the Decision really PAUSEs that Activity",
          display.get("decision") == "PAUSE"
          and driver.activity_status(chain, activity_ref) == "PAUSED"
          and driver.foreground(chain) == "", display)
    check("1.4 an unconditional PAUSE emits no resume eligibility",
          driver.eligibilities(chain, activity_ref) == [])
    shown_id = str(shown.get("candidate_id") or "")

    writes_before = int(chain.writer.call_count)
    after = window(chain, 6, "start_named_extra", {"item_ref": item_ref})
    check("1.5 a due item on the PAUSED work cannot produce a fresh START",
          after.get("decision") == "NO_ACTION", after)
    check("1.6 the source reports PAUSED_TARGET_REQUIRES_RESUME",
          chain.cycle.artifact_source.last_extra_skip_reasons.get(item_ref)
          == "PAUSED_TARGET_REQUIRES_RESUME",
          chain.cycle.artifact_source.last_extra_skip_reasons)
    check("1.7 the item still exists and is still due (only the candidate is withheld)",
          item_ref in [row.item_ref for row in chain.cycle.future_items.due(driver.iso(driver.at(6)))],
          [row.item_ref for row in chain.cycle.future_items.open_items()])
    check("1.8 no fitem candidate is offered in the final request",
          item_ref not in [str(c.get("source_ref") or "")
                           for c in (cognition.last_candidates or [])],
          [c.get("source_ref") for c in (cognition.last_candidates or [])])
    check("1.9 a no-object project option is unaffected by this filter",
          any(str(ref).startswith("popt:") for ref in driver.extra_items(chain)),
          sorted(driver.extra_items(chain)))
    stale = chain.cycle.runtime.candidate_read.get_candidate(shown_id) or {}
    check("1.10 the displayed candidate is withdrawn before adoption",
          str(stale.get("invalidation_reason") or "") == "PAUSED_TARGET_REQUIRES_RESUME",
          stale)
    activities = activity_on(chain, seed)
    check("1.11 there is still exactly one Activity for the work, and it is PAUSED",
          len(activities) == 1 and str(activities[0].get("status") or "") == "PAUSED",
          activities)
    check("1.12 no version, writer call or eligibility was produced",
          driver.head_version(chain, seed) == head_before
          and driver.version_count(chain, seed) == versions_before
          and int(chain.writer.call_count) == writes_before
          and driver.eligibilities(chain, activity_ref) == [],
          {"head": driver.head_version(chain, seed)})
    out["activity_ref"] = activity_ref
    return out


# ---------------------------------------------------------------------------------------------
# 场景 2：WAITING + 正式 RESUME
# ---------------------------------------------------------------------------------------------


def scenario_waiting(chain, cognition) -> dict:
    out: dict = {"phase": "waiting"}
    seed = str(chain.data["artifact_id"])
    started = window(chain, 0, "start_seed")
    activity_ref = str(started.get("activity_ref") or "")
    check("2.1 official START really writes one version",
          started.get("outcome") == "ACTED", started)
    item_ref = propose_on_artifact(chain, seed, 1, 3)
    out["item_ref"] = item_ref

    parked = window(chain, 4, "park", {"in_minutes": 1})
    shown = shown_candidate(chain, cognition, item_ref)
    check("2.2 the due item is displayed before the work is set down",
          bool(shown) and item_ref in driver.extra_items(chain), shown)
    check("2.3 the real WAIT leaves the work WAITING and the slot free",
          parked.get("decision") == "WAIT"
          and driver.activity_status(chain, activity_ref) == "WAITING"
          and driver.foreground(chain) == "", parked)
    shown_id = str(shown.get("candidate_id") or "")
    versions_before = driver.version_count(chain, seed)
    writes_before = int(chain.writer.call_count)

    back = window(chain, 5, "resume_current")
    check("2.4 the due item is no longer offered as a fresh START",
          item_ref not in driver.extra_items(chain)
          and chain.cycle.artifact_source.last_extra_skip_reasons.get(item_ref)
          == "PAUSED_TARGET_REQUIRES_RESUME",
          chain.cycle.artifact_source.last_extra_skip_reasons)
    stale = chain.cycle.runtime.candidate_read.get_candidate(shown_id) or {}
    check("2.5 the previously displayed candidate is withdrawn before adoption",
          str(stale.get("invalidation_reason") or "") == "PAUSED_TARGET_REQUIRES_RESUME",
          stale)
    offers = [c for c in (cognition.last_candidates or [])
              if c.get("candidate_kind") == "RESUME_ACTIVITY"
              and "RESUME" in (c.get("legal_decisions") or [])]
    check("2.6 the owner's own TIME_AT opens exactly one RESUME offer",
          len(offers) == 1, [c.get("candidate_kind") for c in (cognition.last_candidates or [])])
    check("2.7 a new Decision RESUME the same Activity, not a second one",
          back.get("decision") == "RESUME"
          and str(back.get("activity_ref") or "") == activity_ref
          and driver.activity_status(chain, activity_ref) == "ACTIVE"
          and len(activity_on(chain, seed)) == 1, back)
    check("2.8 the eligibility is consumed exactly once",
          len(driver.eligibilities(chain, activity_ref, "CONSUMED")) == 1
          and driver.eligibilities(chain, activity_ref, "OPEN") == [])
    check("2.9 RESUMEd continuation writes one version on the same Activity",
          driver.version_count(chain, seed) == versions_before + 1
          and int(chain.writer.call_count) == writes_before + 1,
          {"versions": driver.version_count(chain, seed)})
    out["activity_ref"] = activity_ref
    return out


# ---------------------------------------------------------------------------------------------
# 场景 3：正控（目标可续、没有挂起活动）
# ---------------------------------------------------------------------------------------------


def scenario_continuable(chain, cognition) -> dict:
    out: dict = {"phase": "continuable"}
    seed = str(chain.data["artifact_id"])
    started = window(chain, 0, "start_seed")
    activity_ref = str(started.get("activity_ref") or "")
    check("3.1 official START really writes one version",
          started.get("outcome") == "ACTED", started)
    abandoned = window(chain, 1, "abandon_current")
    check("3.2 the Activity is closed out while the work stays ACTIVE",
          abandoned.get("decision") == "ABANDON"
          and driver.activity_status(chain, activity_ref) == "ABANDONED", abandoned)
    item_ref = propose_on_artifact(chain, seed, 2, 4)
    out["item_ref"] = item_ref
    versions_before = driver.version_count(chain, seed)

    taken = window(chain, 6, "start_named_extra", {"item_ref": item_ref})
    shown = shown_candidate(chain, cognition, item_ref)
    check("3.3 a due item on a continuable work is still offered with a legal START",
          bool(shown) and "START" in (shown.get("legal_decisions") or [])
          and item_ref in driver.extra_items(chain), shown)
    check("3.4 the formal START is adopted and the real owner writes a new version",
          taken.get("decision") == "START" and taken.get("outcome") == "ACTED"
          and driver.version_count(chain, seed) == versions_before + 1, taken)
    check("3.5 the work has exactly one ACTIVE Activity afterwards",
          len(activity_on(chain, seed)) == 1
          and str(activity_on(chain, seed)[0].get("status") or "") == "ACTIVE")
    out["activity_ref"] = str(taken.get("activity_ref") or "")
    return out


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def _run(name: str, fn) -> dict:
    print("SCENARIO", name)
    root = driver._fresh_root()
    print("ROOT", root)
    clock = driver.Clock(driver.at(0))
    cognition = ParkedCognition()
    services = None
    chain = None
    shutdown: dict = {}
    try:
        driver.prepare_fixture(root)
        services = driver._start_services(root / "supply")
        chain = driver.open_chain(root, services, clock, cognition)
        cognition.seed_artifact = str(chain.data["artifact_id"])
        return fn(chain, cognition)
    finally:
        if chain is not None:
            try:
                driver.close_chain(chain)
            except Exception:  # noqa: BLE001 - reported, never hidden
                pass
        if services is not None:
            try:
                shutdown = driver._stop_services(services)
            except Exception as exc:  # noqa: BLE001
                print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        check("%s: our own Supply owner stops in the production order" % name,
              bool(shutdown) and str(shutdown.get("socket_handling") or "") != ""
              and not Path(str(shutdown.get("socket_path") or "/nonexistent")).exists(),
              shutdown)


def main() -> int:
    facts: dict = {}
    try:
        facts["paused"] = _run("paused", scenario_paused)
        facts["waiting"] = _run("waiting", scenario_waiting)
        facts["continuable"] = _run("continuable", scenario_continuable)
    except BaseException as exc:  # a real failure is reported as one
        print("SMOKE_ABORTED", type(exc).__name__, str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    failed = [row for row in CHECKS if not row[1]]
    print("SUMMARY checks=%d failed=%d" % (len(CHECKS), len(failed)))
    if failed:
        print("SMOKE_FAILED")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
