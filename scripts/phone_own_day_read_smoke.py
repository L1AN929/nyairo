#!/usr/bin/env python3
"""零模型冒烟：手机读今天安排，读的是一整条真实 owner 链留下的状态。

复用 ``scripts/v05_own_day_smoke.py`` 的真 Supply / 正式 Decision / 正式 Action 夹具（不改
原文件，仅 import 它的隔离装配与 window 帮助器）：

* 正式 Decision 建一条 ``fitem`` 并显式排进今天（计划只存引用）；
* operator 正规入口定义一条绑定真实可续 Artifact 的义务；
* 选中义务预留位，真实 Action 成功并留下 owner 回执；
* 另起一个普通续作 Activity（同一作品的新版本），证明它不会被认成计划完成。

然后起真实 ``PhoneHTTPServer`` + ``PhoneService``，只走 HTTP GET
``/api/phone/today/arrangements`` 读取已经存在的状态；不手塞返回，不执行模型，不新增
Action。最后用正规 shutdown/drain/socket guard 关闭自有服务，再关闭隔离链。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_own_day_read_smoke.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_day_plan as ldp  # noqa: E402
import v05_own_day_smoke as own_smoke  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

PLAN_TITLE = "把水井那一段补记一笔"
OBLIGATION_TITLE = "上午先把导言读完并写一句"


def _phone_root(chain) -> Path:
    """A phone root whose nested Life instance is the chain's own instance (same state root).

    ``demo.prepare`` would have written ``<phone>/one-day/instance.json``; this fixture writes
    the same instance document (a copy of the one the chain already prepared) and nothing else.
    The recorded absolute ``NYA02_RESIDENT_STATE`` is what the phone's read resolves.
    """

    root = Path(tempfile.mkdtemp(prefix="phone-own-day-read-"))
    nested = root / "one-day"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "instance.json").write_text(
        json.dumps(chain.data, ensure_ascii=False), encoding="utf-8")
    return root


def _instance(chain, phone_root: Path) -> dict:
    return {
        "root": str(phone_root),
        "subject": str(chain.data["subject_id"]),
        "world_home": str(chain.root / "world-home"),
        "supply_socket": str(chain.services.supply_path),
        "private_items_artifact_id": str(chain.data.get("private_items_artifact_id") or ""),
        "artifact_grant_id": str(chain.data["artifact_grant_id"]),
        "workspace_id": str(chain.data["workspace_id"]),
    }


def _start_phone(chain, phone_root: Path):
    service = phone_service.PhoneService(
        _instance(chain, phone_root), project_port=chain.port)
    server = phone_server.PhoneHTTPServer(
        ("127.0.0.1", 0), service=service, csrf_token="smoke-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _tool_profile(phone_root: Path, port: int, *, private_artifacts: bool) -> Path:
    """A real CLI profile the way ``chiyo_phone`` loads it: loopback phone + entry binding."""

    profile = phone_root / "profile"
    (profile / "chiyo").mkdir(parents=True, exist_ok=True)
    section = {
        "enabled": True,
        "base_url": "http://127.0.0.1:%d" % int(port),
        "profile_home": str(phone_root),
    }
    if private_artifacts:
        section["private_artifacts"] = True
    (profile / "chiyo" / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.getuid(),
        # Restricted memory keeps the private-artifact permit a second, explicit key.
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": section,
    }, ensure_ascii=False), encoding="utf-8")
    return profile


def _tool_call(profile: Path, action: str) -> dict:
    return json.loads(phone_tool.phone_handler({"action": action},
                                               hermes_home=str(profile)))


def _tool_reads(phone_root: Path, port: int) -> dict:
    """The same two reads through the real ``chiyo_phone`` handler, not a parallel helper."""

    restricted = _tool_profile(phone_root, port, private_artifacts=False)
    refusals = {action: _tool_call(restricted, action).get("reason_code")
                for action in ("today", "today_arrangements")}
    permitted = _tool_profile(phone_root, port, private_artifacts=True)
    return {"refusals": refusals,
            "today": _tool_call(permitted, "today"),
            "arrangements": _tool_call(permitted, "today_arrangements")}


def _get(server, path: str) -> tuple[int, dict]:
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d%s" % (port, path), timeout=15) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _stop_phone(server) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(chain, state_root: Path) -> dict:
    files = [
        state_root / ldp.PLAN_FILENAME,
        state_root / ldp.OBLIGATIONS_FILENAME,
        state_root / "action_reality_state.json",
        state_root / "action_reality_journal.jsonl",
        state_root / "agency_decision_state.json",
        state_root / "agency_decision_journal.jsonl",
    ]
    return {
        "files": {str(path.name): (_hash(path) if path.exists() else None)
                  for path in files},
        "seed_head": own_smoke.seed_head(chain),
        "activities": sorted(str(ref) for ref in
                             (chain.runtime.canonical_state().get("activities") or {})),
    }


def _main_scene(tag: str) -> dict:
    chain = own_smoke.build_chain(tag)
    server = None
    phone_root = None
    facts: dict = {"plan": {}, "obligation": {}, "activities": {}, "read": {},
                   "negative": {}, "closure": {}}
    try:
        seed = own_smoke.work_ref(chain.data["artifact_id"])
        day = ldp.business_date(own_smoke.iso(own_smoke.at(0)))
        facts["refs"] = {"seed_artifact": seed}

        # 两个真实 private 标记占满 2 个常规 question 位；否则到期事项自己的普通选项会把
        # 同目标的今日预留位按 dedupe 规则折掉（那是既有规则，不是本夹具的 bug）。
        own_smoke.mark_unclear(chain, marker_id="unclear:phone-read:1",
                               note="第三章为什么每次写到这里就停？")
        own_smoke.mark_unclear(chain, marker_id="unclear:phone-read:2",
                               note="村里那口井到底是谁修的？")

        # ① 正式 Decision 建一条带真实作品的 fitem，到点后显式排进今天。
        own_smoke.window(
            chain, own_smoke.at(0),
            later_item={"title": PLAN_TITLE, "in_minutes": 5, "source_refs": [seed]})
        item = next((row.to_dict() for row in chain.cycle.future_items.open_items()
                     if str(row.title or "") == PLAN_TITLE), {})
        item_ref = str(item.get("item_ref") or "")
        # 事项在 at(5) 到期，due 集合变化本身就是这一轮的真实集合变化。
        own_smoke.window(chain, own_smoke.at(5), plan_today={"item_ref": item_ref})
        plan_entry = (chain.cycle.day_plan.for_date(day) or [{}])[0]
        facts["refs"]["item_ref"] = item_ref
        facts["refs"]["plan_ref"] = str(plan_entry.get("plan_ref") or "")

        # ② operator 正规入口：义务绑定 seed 作品，完成依据是同一对象的版本回执。
        imported = own_smoke.own_day.import_obligation(
            instance=str(chain.instance), date=day, title=OBLIGATION_TITLE,
            target_ref=seed, due_at=own_smoke.iso(own_smoke.at(5)),
            source_ref="operator:phone-read-smoke", operation_key="oblig:phone-read:1",
            target_verifier=chain.port.resume_point, now=own_smoke.iso(own_smoke.at(0)))
        obligation = dict(imported.get("entry") or {})
        obligation_ref = str(obligation.get("obligation_ref") or "")
        option_ref = ldp.obligation_option_ref(obligation_ref, seed, day)
        facts["refs"]["obligation_ref"] = obligation_ref
        facts["refs"]["obligation_option"] = option_ref

        # ③ 选中义务预留位：真实 Action/回执；随后放弃前台。
        seed_before = own_smoke.seed_head(chain)
        own_smoke.window(chain, own_smoke.at(6), decision="START",
                         select_source_ref=option_ref)
        receipt = (chain.cycle.last_receipt.to_dict()
                   if chain.cycle.last_receipt is not None else {})
        seed_after = own_smoke.seed_head(chain)
        own_smoke.window(chain, own_smoke.at(7), decision="ABANDON")

        # ④ 另起一个普通续作 Activity：**另一件**真实作品、另一个 Decision。
        #    计划的同一作品已在上一步被另一个 Decision 写出新版本；它不能因此算完成。
        created = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品（手机读夹具）",
            content="另一件。", grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="op:phone-read:other:1")
        # The candidate's own source_ref is ``artifact:`` + the owner's raw id (which already
        # carries its ``artifact:`` prefix); select that exact spelling, never a rewritten one.
        other_ref = "artifact:%s" % str(created.artifact_id)
        facts["refs"]["other_artifact"] = other_ref
        own_smoke.window(
            chain, own_smoke.at(8), decision="START", select_source_ref=other_ref,
            # 一次真实的集合变化，让这一轮不是 QUIET：否则没有候选可选。
            later_item={"title": "第二次续作用的集合变化", "in_minutes": 2})
        own_smoke.window(chain, own_smoke.at(9), decision="ABANDON")

        canonical = chain.runtime.canonical_state()
        activities = dict(canonical.get("activities") or {})
        # 义务完成对应的 Activity 以 **AR-0 持久 Action** 的 activity_ref 为准；Activity 自己的
        # decision_ref 可能为空（START 也可由 adoption_ref 满足），所以不拿标题/指针猜。
        ledger = [dict(row) for row in (chain.runtime.action_read.list_actions() or [])]
        fulfilling = next(
            (row for row in ledger
             if str(row.get("decision_ref") or "") == str(receipt.get("decision_id") or "")
             and str(row.get("status") or "") == "SUCCEEDED"), {})
        obligation_activity = str(fulfilling.get("activity_ref") or "")
        other_activity = next(
            (str(ref) for ref in activities if str(ref) != obligation_activity), "")
        facts["activities"] = {
            "obligation": obligation_activity,
            "other": other_activity,
            "count": len(activities),
            "seed_head_before": seed_before,
            "seed_head_after": seed_after,
            "receipt_decision": str(receipt.get("decision_id") or ""),
            "receipt_version": str(receipt.get("version_id") or ""),
        }

        # ⑤ 真实 HTTP 读取已存在的状态；两次 GET 前后文件与 head 零变化。
        #    隔离链的时钟是进程内替身；手机读面按实例自己的业务时钟文件取日期，所以这里把
        #    同一业务时刻落到 ``fastday/fastday_clock.json``（真实压缩实例本来就有这份文件），
        #    不把墙钟混进读取。
        state_root = Path(chain.cycle.state_root)
        clock_path = state_root / "fastday" / "fastday_clock.json"
        clock_path.parent.mkdir(parents=True, exist_ok=True)
        clock_path.write_text(json.dumps({
            "kind": "explicit_compressed_demo_clock", "schema_version": 1,
            "mode": "COMPRESSED_DEMO", "rate": 12.0, "state": "STOPPED",
            "domain_id": "fastday:run:phone-read-smoke",
            "run_id": "run:phone-read-smoke",
            "logical_anchor": own_smoke.iso(own_smoke.at(0)),
            "logical_checkpoint": own_smoke.iso(own_smoke.at(9)),
            "horizon_logical": own_smoke.iso(own_smoke.at(0) + timedelta(days=1)),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        phone_root = _phone_root(chain)
        server = _start_phone(chain, phone_root)
        before = _snapshot(chain, state_root)
        today_status, today_payload = _get(server, "/api/phone/today")
        status1, payload1 = _get(server, "/api/phone/today/arrangements")
        status2, payload2 = _get(server, "/api/phone/today/arrangements")
        # 同一个真实手机服务：既走 HTTP，又走模型实际可调的 ``chiyo_phone`` handler。
        tool = _tool_reads(phone_root, server.server_address[1])
        after = _snapshot(chain, state_root)
        data = payload1.get("data") or {}
        tool_data = (tool["arrangements"].get("result") or {}).get("data") or {}
        tool_today_data = (tool["today"].get("result") or {}).get("data") or {}
        tool_plan = next(
            (row for row in (tool_data.get("plan") or {}).get("entries") or []
             if row.get("plan_ref") == facts["refs"]["plan_ref"]), {})
        tool_obligation = next(
            (row for row in (tool_data.get("obligations") or {}).get("entries") or []
             if row.get("obligation_ref") == obligation_ref), {})
        facts["tool"] = {
            "restricted_refusals": tool["refusals"],
            "today_ok": bool(tool["today"].get("ok")),
            "today_status": tool["today"].get("status"),
            "today_http_status": tool["today"].get("http_status"),
            "arrangements_ok": bool(tool["arrangements"].get("ok")),
            "arrangements_http_status": tool["arrangements"].get("http_status"),
            "today_matches_http": tool["today"].get("result") == today_payload,
            "arrangements_matches_http": tool["arrangements"].get("result") == payload1,
            "today_activity_count": tool_today_data.get("activity_count"),
            "today_work_count": tool_today_data.get("work_count"),
            "today_activity_refs": sorted(
                str(row.get("ref") or "") for row in tool_today_data.get("activities") or []),
            "plan": {k: tool_plan.get(k) for k in
                     ("plan_ref", "definition_status", "execution")},
            "obligation": {k: tool_obligation.get(k) for k in ("obligation_ref", "result")},
            "message": tool["arrangements"].get("message"),
        }

        plan_row = next((row for row in (data.get("plan") or {}).get("entries") or []
                         if row.get("plan_ref") == facts["refs"]["plan_ref"]), {})
        obligation_row = next(
            (row for row in (data.get("obligations") or {}).get("entries") or []
             if row.get("obligation_ref") == obligation_ref), {})
        unplanned_refs = [str(row.get("ref") or "")
                          for row in data.get("unplanned_activities") or []]
        unclassified_refs = [str(row.get("ref") or "")
                             for row in data.get("unclassified_activities") or []]
        facts["read"] = {
            "http_status": [status1, status2],
            "ok": bool(payload1.get("ok")) and bool(payload2.get("ok")),
            "same_answer": payload1 == payload2,
            "day": data.get("day"),
            "clock_source": (data.get("clock") or {}).get("source"),
            "plan_count": (data.get("plan") or {}).get("count"),
            "obligation_count": (data.get("obligations") or {}).get("count"),
            "plan": {k: plan_row.get(k) for k in
                     ("plan_ref", "item_ref", "definition_status", "execution")},
            "obligation": {k: obligation_row.get(k) for k in
                           ("obligation_ref", "result", "execution", "evidence")},
            "unplanned": unplanned_refs,
            "unclassified": unclassified_refs,
            "activity_count": data.get("activity_count"),
            "sources": data.get("sources"),
            "read_only": data.get("read_only"),
            "zero_write": before == after,
            "before": before,
            "after": after,
        }

        # ⑥ 来源不可读：具名不可用，不是空日；恢复后仍可读。
        plan_path = state_root / ldp.PLAN_FILENAME
        saved = plan_path.read_bytes()
        plan_path.write_text("not json", encoding="utf-8")
        broken_status, broken = _get(server, "/api/phone/today/arrangements")
        plan_path.write_bytes(saved)
        restored_status, restored = _get(server, "/api/phone/today/arrangements")
        facts["negative"] = {
            "broken_status": broken_status,
            "broken_reason": broken.get("reason_code"),
            "broken_plan_status": ((broken.get("data") or {}).get("plan") or {}).get("status"),
            "restored_status": restored_status,
            "restored_ok": bool(restored.get("ok")),
        }
        return facts
    finally:
        try:
            _stop_phone(server)
        finally:
            facts["closure"].update(own_smoke.close_chain(chain))


def _empty_scene(tag: str) -> dict:
    """A prepared instance with no arrangement and no Activity is honestly empty."""

    chain = own_smoke.build_chain(tag)
    server = None
    try:
        state_root = Path(chain.cycle.state_root)
        canonical = state_root / "canonical_life_state.json"
        if not canonical.exists():
            canonical.write_text(json.dumps({
                "schema_version": "life.activity.canonical.v1", "subject_id": "chiyo",
                "revision": 0, "foreground_activity_ref": None, "activities": {},
            }, ensure_ascii=False), encoding="utf-8")
        phone_root = _phone_root(chain)
        server = _start_phone(chain, phone_root)
        status, payload = _get(server, "/api/phone/today/arrangements")
        data = payload.get("data") or {}
        return {
            "http_status": status,
            "ok": bool(payload.get("ok")),
            "plan": (data.get("plan") or {}).get("entries"),
            "obligations": (data.get("obligations") or {}).get("entries"),
            "unplanned": data.get("unplanned_activities"),
            "unclassified": data.get("unclassified_activities"),
            "activity_count": data.get("activity_count"),
        }
    finally:
        try:
            _stop_phone(server)
        finally:
            own_smoke.close_chain(chain)


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (real Supply socket); run in WSL/Linux")
        return 0
    try:
        facts = _main_scene("phone-read-main")
        empty = _empty_scene("phone-read-empty")
    except BaseException as exc:
        print("SMOKE_ABORTED", type(exc).__name__, str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    print("SAFE_FACTS", json.dumps(facts, ensure_ascii=False, default=str)[:16000])
    print("EMPTY", json.dumps(empty, ensure_ascii=False, default=str))

    read = facts.get("read") or {}
    plan = read.get("plan") or {}
    obligation = read.get("obligation") or {}
    evidence = obligation.get("evidence") or {}
    activities = facts.get("activities") or {}
    negative = facts.get("negative") or {}
    closure = facts.get("closure") or {}
    sources = read.get("sources") or {}
    tool = facts.get("tool") or {}

    checks = [
        ("read: both GETs answer 200/ok with the same read-only envelope",
         read.get("http_status") == [200, 200]
         and read.get("ok") is True
         and read.get("same_answer") is True
         and read.get("read_only") is True
         and read.get("day") == ldp.business_date(own_smoke.iso(own_smoke.at(0)))
         and read.get("clock_source") == "business_clock"),
        ("plan: one self-planned entry, PLANNED is scheduled and execution is UNKNOWN",
         read.get("plan_count") == 1
         and plan.get("plan_ref") == facts["refs"].get("plan_ref")
         and plan.get("item_ref") == facts["refs"].get("item_ref")
         and plan.get("definition_status") == "PLANNED"
         and (plan.get("execution") or {}).get("state") == "UNKNOWN"
         and (plan.get("execution") or {}).get("reason_code") == "NO_EXECUTION_EVIDENCE"),
        ("obligation: DONE identity matches the real receipt/Decision/Activity/version",
         read.get("obligation_count") == 1
         and obligation.get("result") == "DONE"
         and evidence.get("decision_id") == activities.get("receipt_decision")
         and evidence.get("decision_id")
         and evidence.get("activity_ref") == activities.get("obligation")
         and evidence.get("version_id") == activities.get("receipt_version")
         and evidence.get("evidence_path") == ldp.EVIDENCE_PATH_PERSISTED),
        ("activities: the obligation activity is associated; only the other one is unplanned",
         read.get("activity_count") == activities.get("count")
         and activities.get("obligation") not in (read.get("unplanned") or [])
         and activities.get("obligation") not in (read.get("unclassified") or [])
         and read.get("unplanned") == [activities.get("other")]
         and read.get("unclassified") == []),
        ("plan: a newer version of the same work by another Decision does not complete it",
         activities.get("seed_head_before") != activities.get("seed_head_after")
         and (plan.get("execution") or {}).get("state") != "DONE"),
        ("read: plan/obligation/Action/Decision files and the work head are byte-identical",
         read.get("zero_write") is True),
        ("sources: plan/obligations/activities/execution are all readable",
         all((sources.get(name) or {}).get("state") == "ok"
             for name in ("plan", "obligations", "activities", "execution"))),
        ("negative: an unreadable plan source is named, never an empty day; restore works",
         negative.get("broken_status") == 503
         and negative.get("broken_reason") == "DAY_PLAN_STATE_CORRUPT"
         and negative.get("broken_plan_status") == "unavailable"
         and negative.get("restored_status") == 200
         and negative.get("restored_ok") is True),
        ("empty: a prepared day with nothing arranged is honestly empty",
         empty.get("http_status") == 200
         and empty.get("ok") is True
         and empty.get("plan") == []
         and empty.get("obligations") == []
         and empty.get("unplanned") == []
         and empty.get("unclassified") == []
         and empty.get("activity_count") == 0),
        ("tool: restricted memory refuses both today reads until phone.private_artifacts",
         tool.get("restricted_refusals") == {
             "today": "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED",
             "today_arrangements": "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED"}),
        ("tool: chiyo_phone reaches both reads on the real service and matches HTTP",
         tool.get("today_ok") is True
         and tool.get("arrangements_ok") is True
         and tool.get("today_http_status") == 200
         and tool.get("arrangements_http_status") == 200
         and tool.get("today_matches_http") is True
         and tool.get("arrangements_matches_http") is True
         and tool.get("today_activity_count") == read.get("activity_count")),
        ("tool: arrangements keeps PLANNED/UNKNOWN apart from the real DONE obligation",
         (tool.get("plan") or {}).get("definition_status") == "PLANNED"
         and ((tool.get("plan") or {}).get("execution") or {}).get("state") == "UNKNOWN"
         and (tool.get("obligation") or {}).get("result") == "DONE"
         and "不是「还没开始」" in str(tool.get("message") or "")),
        ("closure: services stopped through the normal drain/socket guard",
         (closure.get("services") or {}).get("drained") is True
         and bool((closure.get("services") or {}).get("socket_handling"))),
    ]
    failed = 0
    for name, ok in checks:
        print("CHECK %-84s %s" % (name, "PASS" if ok else "FAIL"))
        failed += 0 if ok else 1
    print("SUMMARY checks=%d failed=%d" % (len(checks), failed))
    if failed:
        print("SMOKE_FAILED")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
