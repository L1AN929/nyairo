#!/usr/bin/env python3
"""Build the v0.1 profile's chat session and memory evidence with the v0.1 code.

Run by ``run_t20_legacy.sh build`` as the profile owner, after the *old*
``scripts/setup_profile.py`` has written ``<HERMES_HOME>/chiyo/config.json``:

    HERMES_HOME=<profile> NYA02_T20_OLD=<old tree> python3 t20_build_old_state.py

Everything here goes through the old release's own entry points:

* ``chiyo_bundle.hermes_plugin.services()``  -- the same instance the old plugin registers
  with the Hermes CLI, built from ``<HERMES_HOME>/chiyo/config.json``;
* ``Instance.chat()`` / ``Instance.confirm_visible()`` -- the old turn pipeline, which writes
  the M0 user and assistant events and persists the conversation;
* ``chiyo_bundle.hermes_plugin.memory_command()`` -- the old ``/chiyo_memory`` command, which
  writes its own M0 event and then drives ``components/native/memory_control.py``'s
  ``MemoryControlLedger.command()``.

No JSON is hand-written: the model stand-in is the same deterministic substitution the old
release's own ``tests/cli_exit_child.py`` uses (``HermesCompletionProvider.complete`` is
replaced for the duration of the turn), so the turn pipeline, the evidence writer and the
control ledger are all real code, and no provider is needed.

The last line of stdout is ``T20_FACTS {json}``: what the old code produced, as read back from
the old code's own stores.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

OLD = Path(os.environ["NYA02_T20_OLD"]).resolve()
sys.path[:0] = [str(OLD), str(OLD / "vendor/hermes"),
                str(OLD / "components/native"), str(OLD / "components/native/src")]

TURNS = [
    ("我的旧版暗号是石榴936，这条要留着。", "记住了：石榴936。"),
    ("第二句：旧版记下的第二条内容，等会儿要停用。", "好，我记下了。"),
]
CORRECTION = "我的旧版暗号改成了桃花628。"


def m0_event_id(m0_db: str, conversation: str, text_fragment: str) -> str:
    """Read the event id the old evidence store assigned to one user turn.

    Read-only: M0 is opened ``mode=ro`` so looking at it cannot create, journal or modify it.
    """
    con = sqlite3.connect("file:%s?mode=ro" % m0_db, uri=True)
    try:
        row = con.execute(
            "select event_id from evidence_events where conversation_id=? and speaker='user'"
            " and content like ? order by rowid limit 1",
            (conversation, "%" + text_fragment + "%")).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit("M0_EVENT_NOT_FOUND: %r" % text_fragment)
    return str(row[0])


def list_blocks(text: str) -> dict[str, str]:
    """The old ``/chiyo_memory list`` view, as {event id: content}."""
    blocks, current = {}, None
    for line in text.splitlines():
        if current is not None and not line.strip() and current in blocks:
            current = None
            continue
        if current is None and line.strip() and " " not in line.strip():
            current = line.strip()
            blocks[current] = ""
        elif current is not None:
            blocks[current] = (blocks[current] + "\n" + line).strip()
    return blocks


def main() -> int:
    from chiyo_bundle import hermes_plugin as plugin

    svc = plugin.services()
    if svc is None:
        raise SystemExit("OLD_SERVICES_UNAVAILABLE: the old plugin built no instance")
    facts: dict = {"state_root": str(svc.state), "owner": svc.owner,
                   "conversation_id": svc.binding["conversation_id"],
                   "paths": dict(svc.paths), "turns": []}

    for index, (user_text, reply_text) in enumerate(TURNS, start=1):
        request_id = "t20-turn-%d" % index
        reply = SimpleNamespace(content=reply_text, usage={}, finish_reason="stop")
        with patch("chiyo_bundle.host.HermesCompletionProvider.complete",
                   side_effect=lambda messages, _reply=reply: _reply):
            result = svc.chat(user_text, request_id=request_id)
            svc.confirm_visible(request_id)
        facts["turns"].append({"index": index, "user_text": user_text, "reply": result["reply"],
                               "turn_id": result["turn_id"], "request_id": request_id,
                               "memory": result.get("memory")})
        print("turn %d: %s" % (index, json.dumps(
            {"reply": result["reply"], "turn_id": result["turn_id"],
             "memory": result.get("memory")}, ensure_ascii=False)))

    # A normal read through the old command: this is the recall/list view the user sees.
    before = plugin.memory_command("list")
    facts["memory_list_before"] = before
    print("== /chiyo_memory list (normal read)\n" + before)
    blocks = list_blocks(before)
    for turn in facts["turns"]:
        turn["m0_event_id"] = m0_event_id(svc.paths["m0_db"], svc.binding["conversation_id"],
                                          turn["user_text"][:12])
    if facts["turns"][0]["m0_event_id"] not in blocks:
        raise SystemExit("MEMORY_LIST_MISSING_TURN: %s" % facts["turns"][0]["m0_event_id"])

    # One real correction and one real stop-recall, both through the old control path.
    facts["correction_reply"] = plugin.memory_command(
        "correct %s %s" % (facts["turns"][0]["m0_event_id"], CORRECTION))
    facts["stop_recall_reply"] = plugin.memory_command(
        "delete %s" % facts["turns"][1]["m0_event_id"])
    print("== /chiyo_memory correct ->", facts["correction_reply"])
    print("== /chiyo_memory delete  ->", facts["stop_recall_reply"])

    after = plugin.memory_command("list")
    facts["memory_list_after"] = after
    print("== /chiyo_memory list (after the controls)\n" + after)

    con = sqlite3.connect("file:%s?mode=ro" % (svc.state / "memory-controls.sqlite"), uri=True)
    con.row_factory = sqlite3.Row
    try:
        facts["controls"] = [dict(row) for row in con.execute(
            "select operation_id, owner, conversation, kind, target_event, replacement_event,"
            " created_at from memory_controls order by rowid")]
    finally:
        con.close()
    print("== control ledger rows: " + json.dumps(facts["controls"], ensure_ascii=False))

    facts["health"] = svc.health()
    facts["storage"] = svc.storage_policy()
    projection = svc.native.memory_controls.projection()
    facts["memory_controls_healthy"] = projection.healthy
    facts["control_projection"] = {
        "controls": projection.controls,
        "blocked_events": sorted(projection.blocked_events),
        "blocked_turns": sorted(projection.blocked_turns),
        "corrected_events": dict(projection.corrected_events),
        "history_after": projection.history_after,
    }
    facts["m0_event_count"] = sqlite3.connect(
        "file:%s?mode=ro" % svc.paths["m0_db"], uri=True).execute(
        "select count(*) from evidence_events").fetchone()[0]

    # The conversation the old turn pipeline persisted: the chat session on disk.
    chat_dir = svc.state / "chat"
    sessions = []
    for path in sorted(chat_dir.rglob("*")):
        if path.is_file():
            sessions.append({"path": str(path), "bytes": path.stat().st_size,
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "records": len([line for line in path.read_text(
                                 encoding="utf-8").splitlines() if line.strip()])})
    facts["chat_session_files"] = sessions
    if not sessions:
        raise SystemExit("NO_CHAT_SESSION_PERSISTED at %s" % chat_dir)
    svc.requests.row_factory = sqlite3.Row
    facts["requests"] = [dict(row) for row in svc.requests.execute(
        "select id, status, source from requests order by rowid")]

    print("== chat session on disk: " + json.dumps(sessions, ensure_ascii=False))
    print("== health: " + json.dumps(facts["health"], ensure_ascii=False))

    plugin.close_services()
    print("T20_FACTS " + json.dumps(facts, ensure_ascii=False))
    sys.stdout.flush()
    # The old Life owner starts a dispatcher thread; the old Hermes CLI leaves through its own
    # one-shot exit for the same reason.  Nothing is left unwritten: the report above is
    # flushed and every store was committed by the old code itself.
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
