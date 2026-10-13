#!/usr/bin/env python3
"""T20 checks 1a and 2-8: the CURRENT v0.2 code against the OLD v0.1 profile.

Run by ``t20_verify.sh`` as the profile's own owner (the uid the old
``setup_profile.py`` recorded), with ``HERMES_HOME`` pointing at the profile the
OLD release built.  The Hermes CLI part of check 1 is done by the shell driver
(it needs the Hermes venv interpreter, which only root can reach here).

Every check prints one ``T20_CHECK <label> PASS|FAIL <real values>`` line.  The
script exits non-zero if any check failed.  Nothing is written by hand: the
stores are the old code's, read through the current code's own modules.
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

REPO = Path(os.environ["NYA02_REPO"]).resolve()
T20 = Path(os.environ["NYA02_T20"]).resolve()
PROFILE = Path(os.environ["HERMES_HOME"]).resolve()
SOCK = os.environ["NYA02_T20_SOCKET"]
# The Hermes agent code itself is not sealed and is not the code under test here; it is taken
# from wherever the caller points NYA02_HERMES_DEPS (the repository's vendor/hermes).
HERMES_DEPS = Path(os.environ.get("NYA02_HERMES_DEPS", str(REPO / "vendor/hermes"))).resolve()
sys.path[:0] = [str(REPO), str(HERMES_DEPS),
                str(REPO / "components/life/scripts"),
                str(REPO / "components/native"), str(REPO / "components/native/src")]

FACTS = json.loads((T20 / "t20-facts.json").read_text(encoding="utf-8"))
ARTIFACT_FACTS = json.loads((T20 / "old-artifact.json").read_text(encoding="utf-8"))
M0 = Path(FACTS["paths"]["m0_db"])
CONTROLS = Path(FACTS["state_root"]) / "memory-controls.sqlite"
CONVERSATION = FACTS["conversation_id"]
SUBJECT = ARTIFACT_FACTS["subject"]
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
    print("T20_CHECK %s %s %s" % (label, "PASS" if ok else "FAIL", text[:900]), flush=True)
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print("\n== " + title, flush=True)


def show(title: str, value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    print("   " + title + ": " + text[:1200], flush=True)


def readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    con.row_factory = sqlite3.Row
    return con


def m0_rows() -> list[dict]:
    con = readonly(M0)
    try:
        return [dict(row) for row in con.execute("select * from evidence_events order by rowid")]
    finally:
        con.close()


def m0_meta() -> dict:
    con = readonly(M0)
    try:
        migrations = [int(r[0]) for r in con.execute(
            "select version from schema_migrations order by version")]
        table = con.execute("select sql from sqlite_master where type='table'"
                            " and name='evidence_events'").fetchone()
        triggers = sorted(r[0] for r in con.execute(
            "select name from sqlite_master where type='trigger' and tbl_name='evidence_events'"))
    finally:
        con.close()
    return {"migrations": migrations, "table_sql": (table[0] if table else "") or "",
            "triggers": triggers}


def ids_of(path: Path, table: str, key: str) -> list:
    con = readonly(path)
    try:
        return [r[0] for r in con.execute("select %s from %s order by rowid" % (key, table))]
    finally:
        con.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot() -> dict:
    """Files no T20 check writes to: a migration that rewrote them would show here."""
    names = ["config.yaml", "SOUL.md", "chiyo/config.json", "plugins/chiyo/__init__.py",
             "plugins/chiyo/plugin.yaml"]
    return {name: sha256(PROFILE / name) for name in names if (PROFILE / name).exists()}


# ---------------------------------------------------------------------------------------
# the state as the OLD code left it, before any v0.2 module opens it
PRE_M0_META = m0_meta()
PRE_M0_ROWS = m0_rows()
PRE_CONTROL_ROWS = [dict(r) for r in readonly(CONTROLS).execute(
    "select operation_id, kind, target_event, replacement_event from memory_controls order by rowid")]
PRE_M1 = ids_of(Path(FACTS["paths"]["m1_db"]), "episodes", "episode_id")
PRE_FILES = file_snapshot()
print("== old profile before the v0.2 code touches it: M0 migrations=%s rows=%d,"
      " M1 episodes=%d, controls=%d"
      % (PRE_M0_META["migrations"], len(PRE_M0_ROWS), len(PRE_M1), len(PRE_CONTROL_ROWS)), flush=True)

import chiyo_bundle.hermes_plugin as plugin        # noqa: E402  the CURRENT v0.2 code
import chiyo_bundle.instance as current_instance   # noqa: E402

section("[1] the old chat still works under the current v0.2 code")
show("current chiyo_bundle.instance", current_instance.__file__)
check("1a.current-code-is-the-v0.2-tree", str(REPO) in str(current_instance.__file__),
      current_instance.__file__)
svc = plugin.services()
if svc is None:
    print("T20_CHECK 1a.instance-unavailable FAIL the current code built no instance", flush=True)
    raise SystemExit(1)
show("instance state root", str(svc.state))
show("M0 the current code resolved", svc.paths["m0_db"])

old_records = svc.native.store.load(CONVERSATION)
old_blob = json.dumps(old_records, ensure_ascii=False)
check("1a.old-chat-session-loaded", "石榴936" in old_blob,
      "%d record(s); old user turn present=%s" % (len(old_records), "石榴936" in old_blob))

captured: dict = {}


def stand_in(messages):
    captured["messages"] = messages
    return SimpleNamespace(content="v0.2 接着这段旧对话回复：旧暗号我按你的纠正来。",
                           usage={}, finish_reason="stop")


with patch("chiyo_bundle.host.HermesCompletionProvider.complete", side_effect=stand_in):
    turn = svc.chat("在 v0.2 上继续这段旧对话：你还记得旧暗号吗？", request_id="t20-verify-turn-1")
    svc.confirm_visible("t20-verify-turn-1")
prompt = captured.get("messages") or []
show("prompt the current code built",
     [{"role": m.get("role"), "chars": len(m.get("content") or "")} for m in prompt])
user_texts = [m["content"] for m in prompt if m.get("role") == "user"]
system_texts = [m["content"] for m in prompt if m.get("role") == "system"]
show("reply", turn["reply"])
check("1a.new-turn-sent-through-the-old-turn-pipeline",
      any("你还记得旧暗号吗" in t for t in user_texts), [t[:60] for t in user_texts])
# The old profile carries a control record, and the old rule is that a controlled turn is no
# longer shown as history: the current code must apply the same rule to the same store.
history_projection = svc.native.memory_controls.projection()
visible_history = history_projection.history(old_records)
show("history the current code would show her (old rules applied)",
     {"stored": len(old_records), "visible": len(visible_history),
      "blocked_turns": sorted(history_projection.blocked_turns)})
check("1a.old-controls-still-shape-the-history",
      len(old_records) > 0 and len(visible_history) < len(old_records)
      and {str(r.get("turn_id")) for r in old_records} <= set(history_projection.blocked_turns),
      "stored %d -> visible %d" % (len(old_records), len(visible_history)))
new_records = svc.native.store.load(CONVERSATION)
check("1a.new-turn-appended-to-the-old-session", len(new_records) > len(old_records),
      "records %d -> %d" % (len(old_records), len(new_records)))
show("memory recall in the system prompt (corrected memory)",
     [t[-200:] for t in system_texts if "桃花628" in t] or "not injected into this prompt")

section("[2] /chiyo_status and /chiyo_memory still work")
import importlib.util                            # noqa: E402

spec = importlib.util.spec_from_file_location("t20_chiyo_plugin", REPO / "plugins/chiyo/__init__.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CapturingContext:
    """Exactly the surface the Hermes CLI hands a plugin, so register() is the real path."""

    def __init__(self) -> None:
        self.commands: dict = {}
        self.hooks: dict = {}
        self.engine = None
        self.llm = None
        self.unload = None

    def register_command(self, name, handler, description=None, args_hint=None):
        self.commands[name] = handler

    def register_context_engine(self, engine):
        self.engine = engine

    def register_hook(self, name, function):
        self.hooks[name] = function

    def on_unload(self, function):
        self.unload = function


ctx = CapturingContext()
module.register(ctx)
show("commands the current plugin registers", sorted(ctx.commands))
show("hooks", sorted(ctx.hooks))
check("2.chiyo_status-registered", "chiyo_status" in ctx.commands, sorted(ctx.commands))
check("2.chiyo_memory-registered", "chiyo_memory" in ctx.commands, sorted(ctx.commands))
status_text = ctx.commands["chiyo_status"]("")
show("chiyo_status output", status_text)
check("2.chiyo_status-answers-on-the-old-profile",
      "记忆：" in status_text and "生活：" in status_text, status_text[:160])
memory_text = ctx.commands["chiyo_memory"]("list")
show("chiyo_memory list output", memory_text)
check("2.chiyo_memory-answers-on-the-old-profile",
      "石榴936" not in memory_text and "第二条内容" not in memory_text, memory_text[:160])

section("[3] the old M0/M1/M37 data is still readable by the old rules")
from app.evidence import EvidenceStore              # noqa: E402  the CURRENT native code
from app.m1 import EpisodeStore                     # noqa: E402
from app.m2 import M2Store                          # noqa: E402
from app.m3 import M3Store                          # noqa: E402

EvidenceStore(FACTS["paths"]["m0_db"])
EpisodeStore(FACTS["paths"]["m1_db"])
M2Store(FACTS["paths"]["m2_db"])
M3Store(FACTS["paths"]["m3_db"])
post_rows = m0_rows()
post_by_id = {r["event_id"]: r for r in post_rows}
original_ids = [r["event_id"] for r in PRE_M0_ROWS]
identical = [eid for eid in original_ids if post_by_id.get(eid) == next(
    r for r in PRE_M0_ROWS if r["event_id"] == eid)]
show("M0 events (old + appended)", "%d -> %d" % (len(PRE_M0_ROWS), len(post_rows)))
check("3.m0-original-rows-readable-unchanged", len(identical) == len(original_ids),
      "%d/%d identical" % (len(identical), len(original_ids)))
show("M1 episodes (old + appended)", "%d -> %d" % (len(PRE_M1), len(ids_of(
    Path(FACTS["paths"]["m1_db"]), "episodes", "episode_id"))))
check("3.m1-original-episodes-preserved",
      set(PRE_M1) <= set(ids_of(Path(FACTS["paths"]["m1_db"]), "episodes", "episode_id")),
      "%d original episode(s)" % len(PRE_M1))
health = svc.health()
show("health of the current code on the old profile", health)
check("3.m37-resolver-ready-on-the-old-state", health.get("memory_resolver_state") == "READY"
      and health.get("memory_last_error_type") is None, health.get("memory_resolver_state"))
check("3.memory-control-healthy", health.get("memory_control_healthy") is True,
      health.get("memory_control_healthy"))
check("3.life-owner-loaded-from-the-old-state", health.get("life_loaded") is True,
      health.get("life_loaded"))

section("[4] the old correction/stop-recall control is still effective")
from memory_control import MemoryControlLedger      # noqa: E402  the CURRENT control module

ledger = MemoryControlLedger(CONTROLS, M0, owner=FACTS["owner"], conversation=CONVERSATION)
projection = ledger.projection()
targets = {row["target_event"] for row in FACTS["controls"]}
replacement = next(row["replacement_event"] for row in FACTS["controls"] if row["kind"] == "CORRECT")
show("control rows (old code wrote them)", FACTS["controls"])
show("projection through the current code", {
    "healthy": projection.healthy, "controls": projection.controls,
    "blocked_events": sorted(projection.blocked_events),
    "corrected_events": projection.corrected_events,
    "blocked_turns": sorted(projection.blocked_turns)})
check("4.control-ledger-healthy", projection.healthy and projection.controls == len(FACTS["controls"]),
      projection.controls)
check("4.stopped-and-corrected-memories-blocked", targets <= projection.blocked_events,
      sorted(targets - projection.blocked_events))
check("4.correction-text-preserved",
      projection.corrected_events.get(replacement) == "我的旧版暗号改成了桃花628。",
      projection.corrected_events.get(replacement))


class RecallObject:
    """The shape the old projection filters: a recall candidate with its source events."""

    def __init__(self, ids):
        self.source_event_ids = list(ids)


allowed, corrections = projection.recalls(
    [RecallObject([row["target_event"]]) for row in FACTS["controls"]]
    + [RecallObject(["event-not-controlled"])])
show("recall view through the old rules", {
    "allowed": [o.source_event_ids for o in allowed], "corrections": corrections})
check("4.corrected-and-stopped-memories-excluded",
      [o.source_event_ids for o in allowed] == [["event-not-controlled"]],
      [o.source_event_ids for o in allowed])
check("4.correction-replaces-the-stopped-memory",
      corrections == ["我的旧版暗号改成了桃花628。"], corrections)
list_now = plugin.memory_command("list")
check("4.chiyo_memory-list-excludes-them",
      "石榴936" not in list_now and "第二条内容" not in list_now, list_now[:160])

section("[5] the old Supply artifact, its versions and the grant are preserved")
from life_artifact_port import LifeArtifactPort     # noqa: E402  the CURRENT port

artifact = ARTIFACT_FACTS["artifact"]
artifact_id = artifact["artifact_id"]
port = LifeArtifactPort(SOCK, SUBJECT)
head = port.read_head(artifact_id)
view = port.read_artifact(artifact_id)
show("artifact head via the current port", head)
show("artifact read via the current port",
     {"artifact_id": view["artifact"].get("artifact_id"), "title": view["artifact"].get("title"),
      "bytes": len(view["content"] or "")})
check("5.artifact-id-and-head-preserved",
      bool(head) and head["version_id"] == artifact["head_version_id"]
      and view["artifact"].get("artifact_id") == artifact_id,
      {"head": (head or {}).get("version_id"), "expected": artifact["head_version_id"]})
check("5.artifact-content-is-the-old-versions-chain",
      (view["content"] or "") == "# 旧版手记\n\nv0.1 写的第一段。\n\n第二段：旧版补的。\n\n第三段：还是旧版补的。\n",
      repr((view["content"] or "")[:60]))
con = readonly(T20 / "old-data/life_supply.sqlite")
try:
    versions = [dict(r) for r in con.execute(
        "select version_id, version_number, parent_version_id, content_sha256 from"
        " artifact_versions where artifact_id=? order by version_number", (artifact_id,))]
    grants = [dict(r) for r in con.execute(
        "select grant_id, subject, capability, scope, target, use_mode, revocation_state"
        " from permission_grants where grant_id=?", (ARTIFACT_FACTS["grants"][-1]["grant_id"],))]
    workspace = dict(con.execute("select workspace_id, subject_id, lifecycle from workspaces"
                                " where workspace_id=?", (artifact["workspace_id"],)).fetchone())
finally:
    con.close()
show("version history now", versions)
show("artifact grant now", grants)
show("workspace now", workspace)
check("5.version-history-preserved",
      [v["version_id"] for v in versions] == [v["version_id"] for v in ARTIFACT_FACTS["versions"]]
      and [v["content_sha256"] for v in versions]
      == [v["content_sha256"] for v in ARTIFACT_FACTS["versions"]],
      "%d version(s)" % len(versions))
check("5.grant-preserved",
      grants == [g for g in ARTIFACT_FACTS["grants"] if g["grant_id"] == grants[0]["grant_id"]]
      if grants else False,
      grants[0] if grants else "no grant row")
check("5.grant-is-the-one-the-profile-names",
      json.loads((PROFILE / "chiyo/config.json").read_text(encoding="utf-8"))
      ["life_supply_artifact_grant"] == (grants[0]["grant_id"] if grants else None),
      (grants[0]["grant_id"] if grants else None))

section("[6] v0.2 autonomous execution is OFF by default")
from autonomous_life_control import life_state_root, read_status   # noqa: E402
from autonomous_life_runner import AutonomousLifeRunner, AutonomousSwitch   # noqa: E402
import autonomous_life_cycle as cycle_module                       # noqa: E402
install_root = Path(FACTS["state_root"]) / "life"
status = read_status(install_root)
switch = AutonomousSwitch(life_state_root(install_root))
show("read_status on the old life state", {
    "install_root": status["install_root"], "life_state_root": status["life_state_root"],
    "autonomous_switch": status["autonomous_switch"],
    "budget_today": status["budget_today"]})
show("switch file", str(switch.path) + (" (absent)" if not switch.path.exists() else ""))
recorded = switch.recorded()
check("6.no-operator-decision-recorded", recorded is None, recorded)
check("6.status-shows-nothing-enabled",
      status["autonomous_switch"]["enabled"] in (None, False),
      status["autonomous_switch"]["enabled"])
env_name = cycle_module.ENV_AUTONOMOUS_ENABLED
stub_runtime = SimpleNamespace(state_root=life_state_root(install_root))
cycle = cycle_module.AutonomousLifeCycle(stub_runtime, env={})
runner = AutonomousLifeRunner(cycle, state_root=life_state_root(install_root), env={})
show("cycle/runner built on the old life state with an empty environment",
     {"cycle.enabled": cycle.enabled, "runner.enabled": runner.enabled,
      "env": {env_name: None}})
check("6.master-switch-default-off",
      os.environ.get(env_name) is None and cycle.enabled is False and runner.enabled is False,
      {"cycle_enabled": cycle.enabled, "runner_enabled": runner.enabled})
# The same answer from the instance the current code assembled on the old profile: the chat
# path's own view of autonomous execution, and the life projection it renders.
check("6.instance-reports-autonomous-off",
      health.get("autonomous_action_enabled") is False and health.get("proactive_enabled") is False
      and (health.get("life_projection") or {}).get("autonomous_enabled") in (None, False),
      {"autonomous_action_enabled": health.get("autonomous_action_enabled"),
       "proactive_enabled": health.get("proactive_enabled"),
       "life_projection.autonomous_enabled":
           (health.get("life_projection") or {}).get("autonomous_enabled")})

section("[7] without a newly granted legal permission an autonomous write is refused")
from life_artifact_port import EffectState          # noqa: E402


def owner_counts() -> dict:
    con = readonly(T20 / "old-data/life_supply.sqlite")
    try:
        return {
            "artifacts": con.execute("select count(*) from managed_artifacts").fetchone()[0],
            "versions": con.execute("select count(*) from artifact_versions").fetchone()[0],
            "head": con.execute("select head_version_id from managed_artifacts where"
                                " artifact_id=?", (artifact_id,)).fetchone()[0],
            "max_version": con.execute("select max(version_number) from artifact_versions"
                                       " where artifact_id=?", (artifact_id,)).fetchone()[0],
        }
    finally:
        con.close()


before = owner_counts()
attempt = port.create_artifact(workspace_id=artifact["workspace_id"],
                               title="T20 未授权写入", content="# 没有新授权\n",
                               grant_id="grant:t20:never-issued",
                               operation_key="op:t20:unauthorized:1")
after = owner_counts()
# This process runs as the profile's own owner, which is the *operator* identity: the old owner
# refuses a command from it by name.  The attempt as the identity that may write at all -- the
# service principal -- is t20_unauthorized_write.py, run by t20_verify.sh.
show("write attempt without a new grant (as the profile owner / operator)",
     {"status": attempt.status, "reason": attempt.reason, "detail": attempt.detail,
      "effect_state": attempt.effect_state})
show("owner state before/after", {"before": before, "after": after})
check("7.owner-identity-write-refused-by-name",
      attempt.effect_state == EffectState.REFUSED and bool(attempt.reason),
      "%s/%s" % (attempt.status, attempt.reason))
check("7.no-write-happened", before == after, {"before": before, "after": after})

section("[8] the v0.1 -> v0.2 migration keeps the original data intact")
post_meta = m0_meta()
show("M0 schema before the v0.2 code", {"migrations": PRE_M0_META["migrations"],
                                        "has_life_origin": "LIFE_VERIFIED_OUTCOME"
                                        in PRE_M0_META["table_sql"],
                                        "triggers": PRE_M0_META["triggers"]})
show("M0 schema after the v0.2 code", {"migrations": post_meta["migrations"],
                                       "has_life_origin": "LIFE_VERIFIED_OUTCOME"
                                       in post_meta["table_sql"],
                                       "triggers": post_meta["triggers"]})
check("8.migration-is-real-and-recorded", 2 in post_meta["migrations"]
      and "LIFE_VERIFIED_OUTCOME" not in PRE_M0_META["table_sql"]
      and "LIFE_VERIFIED_OUTCOME" in post_meta["table_sql"],
      post_meta["migrations"])
check("8.append-only-triggers-recreated",
      {"evidence_no_update", "evidence_no_delete"} <= set(post_meta["triggers"]),
      post_meta["triggers"])
identical = [r["event_id"] for r in PRE_M0_ROWS
             if post_by_id.get(r["event_id"]) == r]
check("8.every-original-m0-row-identical", len(identical) == len(PRE_M0_ROWS),
      "%d/%d rows byte-identical (all columns)" % (len(identical), len(PRE_M0_ROWS)))
appended = [r["event_id"] for r in post_rows if r["event_id"] not in
            {x["event_id"] for x in PRE_M0_ROWS}]
show("appended after the migration", {"count": len(appended),
                                      "rows": [(r["speaker"], r["content"][:24])
                                               for r in post_rows if r["event_id"] in appended]})
check("8.only-appends", len(post_rows) >= len(PRE_M0_ROWS), len(post_rows))
post_controls = [dict(r) for r in readonly(CONTROLS).execute(
    "select operation_id, kind, target_event, replacement_event from memory_controls order by rowid")]
check("8.control-ledger-untouched", post_controls == PRE_CONTROL_ROWS, post_controls)
changed = [name for name, digest in PRE_FILES.items()
           if not (PROFILE / name).exists() or sha256(PROFILE / name) != digest]
check("8.configuration-files-byte-identical", not changed, changed)
print("   (files the checks did not write: %s)" % ", ".join(sorted(PRE_FILES)), flush=True)

plugin.close_services()
print()
if FAILURES:
    print("T20_CHECKS FAILED %d: %s" % (len(FAILURES), ", ".join(FAILURES)), flush=True)
    sys.exit(1)
print("T20_CHECKS PASSED", flush=True)
sys.exit(0)
