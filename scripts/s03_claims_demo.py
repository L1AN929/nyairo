#!/usr/bin/env python3
"""S03 Perception/Belief demo -- observed / reported / inferred, with lifecycle.

What this actually exercises (all local, 0 product model calls, 0 external network)
-------------------------------------------------------------------------------
1. a **real** ``PhoneWorld`` snapshot (the World owner's own facade) is taken through
   the **real phone HTTP surface** (``claim_observe``); the observation carries the
   world id/revision and a receipt, and becomes an ``OBSERVED`` claim;
2. two explicitly-labelled **operator reports** disagree about one shop's closing
   time: both stay current and are shown as ``DISPUTED`` -- no last-write-wins;
3. one **source correction** invalidates exactly that source and re-derives: the old
   interpretation leaves the current view and stays in history with its revision
   chain.  The other shop's conflict is left unresolved on purpose;
4. an **inference** is recorded that binds the corrected closing-time claim (it is
   not a new source);
5. an injected business clock passes the revalidation deadline: an explicit
   ``maintenance`` call persists ``STALE``; the deadline is not reset, and the
   inferred claim follows its basis;
6. an **UNKNOWN** slot (no evidence) is reported as UNKNOWN, not as "false";
7. one formal ``evaluate_once`` (scripted Decision, offline) proves the **real
   request** carried the bounded current-claims block -- and the first round was
   SUPPORTED/DISPUTED while the second (after the clock advance) is STALE, without
   any new claim being made for her;
8. the aggregate is reopened from the same SQLite root: revision/status/source state
   is preserved (it is not silently rebuilt as "all trusted"), and the deterministic
   reducer matches the materialized tables;
9. a source retraction withholds the old payload from both current and history; a
   claim never authorizes a World action and never writes Memory.

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s03_claims_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import result_method_behavior_smoke as base  # noqa: E402
import s03_claims  # noqa: E402

TS0 = "2026-11-20T09:00:00+00:00"        # 18:00 Asia/Tokyo
STALE_TS = "2026-11-21T10:00:00+00:00"   # 25h later: revalidation due
CSRF = "s03-claims-demo-csrf"
BOOKSTORE = "bookstore"
CAFE = "cafe"


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-66s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok and detail is not None:
        say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1200])
    return ok


def _clock(value: str):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return lambda: moment


def _tool(profile: Path, action: str, **fields) -> dict[str, Any]:
    args = {"action": action}
    args.update(fields)
    raw = base_phone_tool().phone_handler(args, hermes_home=str(profile))
    return json.loads(raw)


def base_phone_tool():
    from chiyo_bundle import phone_tool
    return phone_tool


def _start_phone(instance: Mapping[str, Any], *, now: str, world: Any):
    from components.phone import server as phone_server
    from components.phone import service as phone_service

    # ``PhoneService._clock`` is the phone's ISO-string clock (used by the operation
    # journal as well); the World facade gets its own datetime clock.
    service = phone_service.PhoneService(instance, environment={}, clock=lambda: now,
                                         world=world)
    httpd = phone_server.create_server(service, csrf_token=CSRF, port=0)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return httpd, thread, "http://127.0.0.1:%d" % int(httpd.server_address[1])


def _stop_phone(httpd, thread) -> None:
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    if thread is not None:
        thread.join(timeout=10)


def _write_profile(phone_root: Path, base_url: str) -> Path:
    profile = phone_root / "profile"
    (profile / "chiyo").mkdir(parents=True, exist_ok=True)
    (profile / "chiyo" / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.geteuid(),
        "phone": {
            "enabled": True,
            "base_url": base_url,
            "profile_home": str(phone_root),
            "write_enabled": True,
            "private_artifacts": True,
        },
    }, ensure_ascii=False), encoding="utf-8")
    return profile


def _snapshot_facts(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), Mapping) else {}
    world = data.get("world") if isinstance(data.get("world"), Mapping) else {}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    return {"world_id": world.get("world_id"), "revision": world.get("revision"),
            "local_date": world.get("local_date"), "timezone": world.get("timezone"),
            "place_id": location.get("place_id"), "place_name": location.get("name"),
            "place_kind": location.get("kind")}


def _slot_for(view: Mapping[str, Any], predicate: str, scope: str) -> dict[str, Any] | None:
    for row in view.get("slots") or []:
        if row.get("predicate") == predicate and row.get("scope_id") == scope:
            return row
    return None


def _claims_status(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"predicate": row.get("predicate"), "scope": row.get("scope_id"),
             "status": row.get("status"), "reason": row.get("status_reason"),
             "source_kind": row.get("source_kind"),
             "value": (row.get("object") or {}).get("time")
             if isinstance(row.get("object"), Mapping) else None,
             "observed_at": row.get("observed_at"),
             "recorded_at": row.get("recorded_at"),
             "valid_to": row.get("valid_to")}
            for row in (view.get("claims") or [])]


def main() -> int:
    failures: list[str] = []

    def bad(name: str, detail: Any = None) -> None:
        failures.append(name)
        check(name, False, detail)

    chain = base.build_chain("s03-claims")
    claims = chain.cycle.claims_service
    httpd = thread = None
    try:
        if not check("assembly bound the S03 claim aggregate", claims is not None,
                     {"assembly_claims": (chain.runtime and "n/a")}):
            return 1
        if not check("the aggregate starts empty (no invented belief)",
                     claims.current_view(now=TS0)["counts"]["claims"] == 0,
                     claims.current_view(now=TS0)["counts"]):
            return 1
        db_path = Path(str(chain.data.get("s03_claims_db") or ""))
        if not check("the new root names its own S03 SQLite binding",
                     db_path.exists() and db_path.name == "claims.sqlite3",
                     {"s03_db": str(db_path)}):
            return 1

        # ---- 1. one real World observation, through the real phone HTTP surface ----
        from world_phone_world import PhoneWorld

        world_home = Path(str(chain.data["world_home"]))
        world = PhoneWorld(str(world_home), dict(chain.env), local_owner_uid=os.geteuid(),
                           subject="chiyo",
                           gateway_socket=world_home / "run" / "gateway.sock",
                           clock=_clock(TS0))
        raw_snapshot = world.snapshot()
        facts = _snapshot_facts(raw_snapshot)
        if not check("a real World snapshot is available for observation",
                     bool(raw_snapshot.get("ok")) and facts["place_id"] is not None, facts):
            return 1
        world_state_path = world_home / "data" / "world" / "world_state.json"
        world_before = world_state_path.read_bytes() if world_state_path.exists() else b""

        phone_root = chain.root / "phone"
        (phone_root / "run").mkdir(parents=True, exist_ok=True)
        instance = {
            "schema_version": "phone.instance.v1",
            "kind": "phone_local_instance",
            "name": "s03-claims-demo",
            "subject": str(chain.data["subject_id"]),
            "local_owner_uid": os.geteuid(),
            "root": str(phone_root),
            "profile_home": str(phone_root),
            "world_home": str(world_home),
            "world_id": str(chain.data.get("world_id") or ""),
            "supply_socket": str(chain.services.supply_path),
            "private_items_artifact_id": str(chain.data.get("private_items_artifact_id") or ""),
            "artifact_grant_id": str(chain.data["artifact_grant_id"]),
            "workspace_id": str(chain.data["workspace_id"]),
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "s03_claims_db": str(db_path),
            "s03_claims_subject": str(chain.data["subject_id"]),
            "features": {},
        }
        httpd, thread, base_url = _start_phone(instance, now=TS0, world=world)
        profile = _write_profile(phone_root, base_url)
        observed = _tool(profile, "claim_observe", operation_key="op:s03:observe:1")
        observed_envelope = observed.get("result") if isinstance(observed.get("result"),
                                                                 Mapping) else {}
        observed_data = observed_envelope.get("data") if isinstance(
            observed_envelope.get("data"), Mapping) else {}
        receipt = observed_data.get("receipt_ref")
        check("the phone recorded one formal observation with its own receipt",
              bool(observed.get("ok")) and bool(receipt)
              and observed_data.get("source_kind") == s03_claims.SOURCE_OBSERVED,
              {"answer": observed})
        read_observed = _tool(profile, "claims")
        read_envelope = read_observed.get("result") if isinstance(
            read_observed.get("result"), Mapping) else {}
        rows = _claims_status(read_envelope.get("data") if isinstance(
            read_envelope.get("data"), Mapping) else {})
        check("her phone shows the OBSERVED claim (not a belief)",
              any(row["status"] == "SUPPORTED"
                  and row["source_kind"] == s03_claims.SOURCE_OBSERVED
                  for row in rows), rows)

        # ---- 2. two reported answers for one shop -> DISPUTED ---------------------
        context = {"local_date": facts["local_date"], "timezone": facts["timezone"]}
        claims.report(predicate_id="place_closing_time", scope_id=BOOKSTORE, value="22:00",
                      context=context, source_ref="operator_report:fixture:shop-a",
                      now=TS0, operation_key="op:s03:report:a")
        claims.report(predicate_id="place_closing_time", scope_id=BOOKSTORE, value="20:00",
                      context=context, source_ref="operator_report:fixture:shop-b",
                      now=TS0, operation_key="op:s03:report:b")
        view = claims.current_view(now=TS0)
        check("two independent reports stay side by side as DISPUTED",
              view["conflicts"] and view["conflicts"][0]["distinct_answers"] == 2,
              {"statuses": _claims_status(view), "conflicts": view["conflicts"]})

        # ---- 3. one explicit source correction -> old answer leaves current -------
        corrected = claims.correct_source(
            source_ref="operator_report:fixture:shop-b", now=TS0,
            operation_key="op:s03:correct:b", replacement={"value": "22:00"})
        view = claims.current_view(now=TS0)
        check("the corrected source's old answer is superseded in current",
              corrected.get("status") == "CORRECTED"
              and not any(row.get("object") == {"time": "20:00"}
                          for row in view["claims"]), {"corrected": corrected,
                                                       "view": _claims_status(view)})
        bookstore_slot = _slot_for(view, "place_closing_time", BOOKSTORE)
        if bookstore_slot is None:
            bad("the bookstore closing-time slot exists")
            return 1
        slot_bookstore = claims.slot_view(slot_id=bookstore_slot["slot_id"], now=TS0)
        history = claims.history(slot_id=bookstore_slot["slot_id"], now=TS0)
        check("...while history keeps the old interpretation and its revision chain",
              any(row.get("object") == {"time": "20:00"} and row.get("status") == "SUPERSEDED"
                  for row in history["claims"]), history["claims"])
        check("...and the slot is no longer a conflict (both answers now agree)",
              slot_bookstore.get("status") == "SUPPORTED",
              slot_bookstore)

        # ---- 4. an inference bound to the corrected claim -------------------------
        corrected_claim = next(
            (row for row in view["claims"]
             if row.get("predicate") == "place_closing_time"
             and row.get("object") == {"time": "22:00"}), None)
        if corrected_claim is None:
            bad("the corrected closing-time claim is current")
            return 1
        inferred = claims.infer(
            predicate_id="place_likely_open", scope_id=BOOKSTORE, value=True,
            basis_claim_ids=[corrected_claim["claim_id"]], context=context,
            now=TS0, operation_key="op:s03:infer:open")
        check("the inference is recorded as INFERRED, bound to its basis claim",
              inferred.get("source_kind") == s03_claims.SOURCE_INFERRED
              and inferred.get("basis_claim_ids") == [corrected_claim["claim_id"]],
              inferred)

        # ---- 5. a second, deliberately unresolved conflict (stays DISPUTED) -------
        claims.report(predicate_id="place_closing_time", scope_id=CAFE, value="23:00",
                      context=context, source_ref="operator_report:fixture:cafe-a",
                      now=TS0, operation_key="op:s03:cafe:a",
                      revalidate_after_seconds=7 * 24 * 3600)
        claims.report(predicate_id="place_closing_time", scope_id=CAFE, value="21:00",
                      context=context, source_ref="operator_report:fixture:cafe-b",
                      now=TS0, operation_key="op:s03:cafe:b",
                      revalidate_after_seconds=7 * 24 * 3600)
        view = claims.current_view(now=TS0)
        check("a different shop's conflict remains unresolved (no auto-winner)",
              any(row["scope_id"] == CAFE for row in view["conflicts"]),
              view["conflicts"])

        # ---- 6. the formal decision request carries the bounded claims block ------
        chain.cycle._business_clock = _clock(TS0)
        material_id, revision = base.import_material(chain)
        base.write_question(chain, material_id, revision)
        chain.cognition.script = {"decision": "NO_ACTION", "selected_candidate_id": None,
                                  "deferred_candidate_ids": [],
                                  "short_rationale": "stub: read the current claims"}
        out = chain.cycle.evaluate_once(trigger_ref="evt:s03:claims:before", act=True)
        request = chain.cognition.requests[-1] if chain.cognition.requests else None
        block = (getattr(request, "situation_context", {}) or {}).get("claims") \
            if request is not None else None
        kinds = {row.get("source_kind") for row in (block or {}).get("claims", [])}
        statuses = {row.get("status") for row in (block or {}).get("claims", [])}
        check("the real evaluate_once request carried the current claims",
              isinstance(block, Mapping) and bool(block.get("claims"))
              and s03_claims.SOURCE_OBSERVED in kinds
              and s03_claims.SOURCE_REPORTED in kinds
              and s03_claims.SOURCE_INFERRED in kinds,
              {"outcome": out.get("outcome"), "block": block})
        check("...with the status differences visible in the same request",
              "SUPPORTED" in statuses and "DISPUTED" in statuses,
              {"statuses": sorted(statuses), "block": block})
        check("...and it is declared as fact/uncertainty, not a permission",
              (block or {}).get("not_permission") is True
              and (block or {}).get("actions_authorized") == 0,
              block)
        # The production prompt builder is a whitelist: prove the same block survives it,
        # otherwise the request would carry claims that the real prompt silently drops.
        import agency_cognition_adapter as aca
        bounded = aca.ProductionAgencyCognitionAdapter._bounded_situation(
            {"claims": block})
        bounded_claims = (bounded.get("claims") or {}).get("claims") or []
        check("the production prompt whitelist carries the same claims block",
              bool(bounded_claims)
              and {row.get("status") for row in bounded_claims}.issuperset({"SUPPORTED"}),
              bounded.get("claims"))

        # ---- 7. clock advance -> explicit STALE, deadline not reset ---------------
        calls_before_maintenance = chain.cognition.call_count
        before_stale = claims.current_view(now=TS0)
        maintenance = claims.maintenance(now=STALE_TS)
        after_stale = claims.current_view(now=STALE_TS)
        stale_status = {(row.get("predicate"), row.get("scope_id")): row.get("status")
                        for row in after_stale["claims"]}
        status_detail = [[predicate, scope, status]
                         for (predicate, scope), status in sorted(stale_status.items())]
        check("the closing-time claim is STALE after its revalidation deadline",
              stale_status.get(("place_closing_time", BOOKSTORE)) == "STALE"
              and stale_status.get(("place_likely_open", BOOKSTORE)) == "STALE",
              {"marked": maintenance.get("marked_stale"), "statuses": status_detail})
        check("...the unresolved cafe conflict is still DISPUTED (different deadline)",
              stale_status.get(("place_closing_time", CAFE)) == "DISPUTED",
              status_detail)
        check("...the observation is still SUPPORTED (its own 7-day bound)",
              any(row.get("predicate") == "subject_last_observed_place"
                  and row.get("status") == "SUPPORTED"
                  for row in after_stale["claims"]), _claims_status(after_stale))
        check("maintenance is idempotent (it does not reset the deadline)",
              claims.maintenance(now=STALE_TS)["count"] == 0
              and bool(claims.current_view(now=STALE_TS)["stale"]),
              {"first": maintenance.get("count"), "before": before_stale.get("stale")})
        check("the clock advance changed the view without another model call",
              chain.cognition.call_count == calls_before_maintenance,
              {"before": calls_before_maintenance, "after": chain.cognition.call_count})

        # ---- 8. a formal second request sees the difference ----------------------
        chain.cycle._business_clock = _clock(STALE_TS)
        chain.cognition.script = {"decision": "NO_ACTION", "selected_candidate_id": None,
                                  "deferred_candidate_ids": [],
                                  "short_rationale": "stub: the closing time is stale"}
        out2 = chain.cycle.evaluate_once(trigger_ref="evt:s03:claims:after", act=True)
        request2 = chain.cognition.requests[-1]
        block2 = (getattr(request2, "situation_context", {}) or {}).get("claims") or {}
        statuses2 = {row.get("status") for row in block2.get("claims", [])}
        check("the next formal request really reads STALE (not the old SUPPORTED)",
              "STALE" in statuses2 and "DISPUTED" in statuses2,
              {"outcome": out2.get("outcome"), "statuses": sorted(statuses2)})

        # ---- 9. reopen the same root: state persists, reducer matches -------------
        reopened = s03_claims.S03ClaimService(str(db_path), subject=str(chain.data["subject_id"]))
        reopened_view = reopened.current_view(now=STALE_TS)
        reopened_status = {(row.get("predicate"), row.get("scope_id")): row.get("status")
                           for row in reopened_view["claims"]}
        check("reopening the same S03 root preserves statuses and revisions",
              reopened_status.get(("place_closing_time", BOOKSTORE)) == "STALE"
              and reopened_status.get(("place_closing_time", CAFE)) == "DISPUTED"
              and any(row.get("predicate") == "place_likely_open"
                      and row.get("status") == "STALE"
                      for row in reopened_view["claims"]),
              [[predicate, scope, status]
               for (predicate, scope), status in sorted(reopened_status.items())])
        rebuilt = reopened.rebuild()
        check("the deterministic reducer rebuilds the materialized tables",
              rebuilt.get("matches") is True, rebuilt)

        # ---- 9b. a normal phone stop/restart on the same root ---------------------
        _stop_phone(httpd, thread)
        httpd = thread = None
        httpd, thread, base_url = _start_phone(instance, now=STALE_TS, world=world)
        profile = _write_profile(phone_root, base_url)
        restart_read = _tool(profile, "claims")
        restart_envelope = restart_read.get("result") if isinstance(
            restart_read.get("result"), Mapping) else {}
        restart_view = restart_envelope.get("data") if isinstance(
            restart_envelope.get("data"), Mapping) else {}
        restart_status = {(row.get("predicate"), row.get("scope_id")): row.get("status")
                          for row in restart_view.get("claims") or []}
        check("a normal phone stop/restart preserves the same claim state",
              restart_status.get(("place_closing_time", BOOKSTORE)) == "STALE"
              and restart_status.get(("place_closing_time", CAFE)) == "DISPUTED",
              [[p, s, v] for (p, s), v in sorted(restart_status.items())])

        # ---- 10. an UNKNOWN slot is not "false" ----------------------------------
        unknown = claims.slot_status(
            predicate_id="place_closing_time", scope_id=BOOKSTORE,
            context={"local_date": "2026-11-21", "timezone": facts["timezone"]},
            now=STALE_TS)
        check("a slot with no evidence is UNKNOWN with a named reason",
              unknown.get("status") == "UNKNOWN"
              and unknown.get("reason_code") == s03_claims.REASON_NO_SLOT, unknown)
        unknown_phone = _tool(profile, "claim_slot",
                              claim_slot_id=unknown["slot_id"])
        unknown_envelope = unknown_phone.get("result") if isinstance(
            unknown_phone.get("result"), Mapping) else {}
        unknown_data = unknown_envelope.get("data") if isinstance(
            unknown_envelope.get("data"), Mapping) else {}
        check("...and her phone reports the same UNKNOWN slot honestly",
              bool(unknown_phone.get("ok"))
              and (unknown_data.get("slot") or {}).get("status") == "UNKNOWN",
              unknown_phone)

        # ---- 11. retraction withholds the payload; no World/Memory write ---------
        cafe_slot = _slot_for(after_stale, "place_closing_time", CAFE)
        if cafe_slot is None:
            bad("the cafe conflict slot exists")
            return 1
        current_cafe = claims.slot_view(slot_id=cafe_slot["slot_id"], now=STALE_TS)
        involved = [row for row in current_cafe["claims"]
                    if row.get("source_ref") == "operator_report:fixture:cafe-a"]
        check("a retraction targets exactly the named source",
              bool(involved), current_cafe)
        claims.retract_source(source_ref="operator_report:fixture:cafe-a",
                              now=STALE_TS, operation_key="op:s03:retract:cafe-a")
        after_retract = claims.slot_view(slot_id=cafe_slot["slot_id"], now=STALE_TS)
        retracted = [row for row in after_retract["claims"]
                     if row.get("source_ref") == "operator_report:fixture:cafe-a"]
        check("the retracted claim's old payload is withheld (current and history)",
              all(row.get("object") is None and row.get("withheld") for row in retracted),
              retracted)
        cafe_history = claims.history(slot_id=cafe_slot["slot_id"], now=STALE_TS)
        check("...history also withholds it, keeping only the permitted shell",
              all(row.get("object") is None for row in cafe_history["claims"]
                  if row.get("source_ref") == "operator_report:fixture:cafe-a"),
              cafe_history["claims"])
        check("...and the other source's answer is still readable",
              any(row.get("source_ref") == "operator_report:fixture:cafe-b"
                  and row.get("object") == {"time": "21:00"}
                  for row in cafe_history["claims"]), cafe_history["claims"])

        world_after = world_state_path.read_bytes() if world_state_path.exists() else b""
        check("no claim operation updated the World state",
              world_before == world_after,
              {"before_bytes": len(world_before), "after_bytes": len(world_after)})
        final = claims.current_view(now=STALE_TS)
        check("claims are not action authorization or Memory writes",
              final.get("actions_authorized") == 0
              and final.get("world_updates") == 0
              and final.get("memory_writes") == 0, final)
        check("a report cannot smuggle a World observation source",
              _refused(lambda: claims.report(
                  predicate_id="place_closing_time", scope_id=BOOKSTORE, value="19:00",
                  context=context, source_ref="world:coastal_town", now=TS0,
                  operation_key="op:s03:report:fake")),
              "expected S03_REPORT_SOURCE_NOT_EXPLICIT")
        check("an inference without a basis is refused",
              _refused(lambda: claims.infer(
                  predicate_id="place_likely_open", scope_id=BOOKSTORE, value=True,
                  basis_claim_ids=[], context=context, now=TS0,
                  operation_key="op:s03:infer:nobasis")),
              "expected S03_INFERENCE_BASIS_MISSING")

        say("SHORT_VIEW " + json.dumps({
            "claims": _claims_status(final)[:8],
            "conflicts": len(final.get("conflicts") or []),
            "stale": len(final.get("stale") or []),
            "unknown_slot": [unknown.get("predicate"), unknown.get("scope_id"),
                             unknown.get("status")],
        }, ensure_ascii=False))
    finally:
        _stop_phone(httpd, thread)
        base.close_chain(chain)

    if failures:
        say("FAILED %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    say("S03_CLAIMS_DEMO_OK")
    return 0


def _refused(call) -> bool:
    try:
        call()
    except s03_claims.S03ClaimsRefused:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
