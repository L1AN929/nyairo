#!/usr/bin/env bash
# T20: build a REAL v0.1 instance with the v0.1 code, then hand it to the v0.2 code.
#
# The work order forbids hand-written JSON standing in for an old instance, so this script does
# not fabricate anything: it extracts the real release tag, starts the *old* Supply owner, mints
# the grant with the old owner's own RPC, creates a real ManagedArtifact with a real version
# history through that owner, and lets the *old* `scripts/setup_profile.py` plus the *old*
# plugin command path build the profile (config, chat session, M0 memory, memory control record).
#
#   run_t20_legacy.sh build     # old code: make the v0.1 instance
#   run_t20_legacy.sh verify    # v0.2 code: the eight compatibility checks
#   run_t20_legacy.sh stop      # stop this harness's old owner only
#
# Nothing here touches the real user profile: everything lives under $T20.  The old owner is
# only ever addressed by its own working directory, so another batch's owner (a different cwd)
# is never signalled.
set -uo pipefail

T20="${NYA02_T20:-/srv/nya02/t20}"
OLD="$T20/old"
PROFILE="$T20/profile"
SOCK="$T20/sock/life-supply.sock"
DATA="$T20/old-data"
REPO="${NYA02_REPO:-/mnt/d/nyairo-v02}"
TAG="${NYA02_T20_TAG:-v0.1.0-rc9}"
SUBJECT="${NYA02_T20_SUBJECT:-t20legacy}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FACTS="$T20/t20-facts.json"
ARTIFACT_FACTS="$T20/old-artifact.json"
OWNER_PIDFILE="$T20/old-owner.pid"
OP_UID="$(id -u nyaop 2>/dev/null || echo 1001)"
SVC_UID="$(id -u nyasvc 2>/dev/null || echo 1002)"

rpc() { python3 "$T20/rpc.py" "$1"; }

# Every process running this harness's old owner, identified by the old owner's own working
# directory.  A pattern match on "supply_service.py" alone would also match the resident owner
# of another batch, which must never be touched.
old_owner_pids() {
  python3 - "$OLD" <<'PY'
import os, sys
old = sys.argv[1]
found = []
for entry in os.listdir('/proc'):
    if not entry.isdigit():
        continue
    try:
        cmd = open('/proc/%s/cmdline' % entry, 'rb').read().replace(b'\0', b' ').decode()
        cwd = os.readlink('/proc/%s/cwd' % entry)
    except OSError:
        continue
    if 'scripts/supply_service.py' in cmd and cwd == old:
        found.append(entry)
print(' '.join(sorted(found, key=int)))
PY
}

stop_old_owner() {
  local pids
  pids="$(old_owner_pids)"
  if [ -n "$pids" ]; then echo "stopping old owners: $pids"; kill -TERM $pids 2>/dev/null || true; fi
  for _ in $(seq 1 40); do [ -z "$(old_owner_pids)" ] && break; sleep 0.25; done
  pids="$(old_owner_pids)"
  if [ -n "$pids" ]; then kill -KILL $pids 2>/dev/null || true; sleep 0.5; fi
  rm -f "$OWNER_PIDFILE"
}

start_old_owner() {
  local mode="${1:-fresh}"
  stop_old_owner
  # A stale owner still holds the write fence on the data root, and the old governance store
  # refuses a second grant with an already-used operation id, so a *build* starts from a fresh
  # data root.  Both facts are the old owner's own named refusals (WRITE_FENCE_HELD,
  # OPERATION_ALREADY_COMMITTED) -- see the run log of a second `build` without this.  A
  # verify run passes `keep`, because the artifact and the grant it checks live there.
  [ "$mode" = fresh ] && rm -rf "$DATA"
  rm -f "$SOCK"
  sleep 1
  mkdir -p "$T20/sock" "$DATA"
  chmod 755 "$T20" "$T20/sock"
  cat > "$T20/old-env.sh" <<EOF
export LIFE_SUPPLY_SOCKET=$SOCK
export LIFE_SUPPLY_DATA_ROOT=$DATA
export LIFE_SUPPLY_OPERATOR_UIDS=$OP_UID
export LIFE_SUPPLY_SERVICE_UIDS=$SVC_UID
export LIFE_SUPPLY_REQUIRE_NON_ROOT=1
export LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION=ON
export LIFE_SUPPLY_SWITCH_WORKSPACE_PROVISION=ON
export LIFE_SUPPLY_SWITCH_PERSONAL_CANDIDATE_CONSUMPTION=ON
EOF
  # `setsid --fork` and `< /dev/null` are not cosmetic: with the plain `... &` form the old
  # owner stays a direct child of this script, and bash then blocks in wait4() on it while
  # exiting, so `run_t20_legacy.sh build` never returns and anything reading its output
  # (a pipe, `tail`, the caller) waits forever -- the harness looks like it hangs.  The
  # daemon writes its own pid first, so the pidfile names the real owner process.
  ( cd "$OLD" && set -a && . "$T20/old-env.sh" && set +a && \
    setsid --fork sh -c 'echo $$ > "$1"; exec python3 scripts/supply_service.py' _ "$OWNER_PIDFILE" \
      > "$T20/old-owner.log" 2>&1 < /dev/null )
  for _ in $(seq 1 60); do [ -S "$SOCK" ] && break; sleep 0.5; done
  [ -S "$SOCK" ] || { echo "OLD_OWNER_NOT_STARTED"; tail -20 "$T20/old-owner.log"; return 1; }
  chmod 0666 "$SOCK"   # harness file-mode workaround; authorization is still SO_PEERCRED
  echo "old owner: pid $(cat "$OWNER_PIDFILE" 2>/dev/null) $(ls -l "$SOCK")"
}

write_helpers() {
  # The release's own RPC helper, so the old instance is spoken to with the old code.
  # The CLI part is guarded: mint.py imports ``call`` from this file, and an unguarded
  # module-level call would re-parse mint.py's own argv and fail the import.
  cat > "$T20/rpc.py" <<'PY'
"""One-line JSON RPC over the Life Supply Unix socket.  Harness helper."""
import json, os, socket, sys


def call(req, socket_path=None, timeout=15.0):
    path = socket_path or os.environ.get("LIFE_SUPPLY_SOCKET",
                                         "/srv/nya02/t20/sock/life-supply.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout); s.connect(path)
    s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
    return json.loads(s.makefile("rb").readline(1_000_000))


if __name__ == "__main__":
    print(json.dumps(call(json.loads(sys.argv[1])), ensure_ascii=False, sort_keys=True))
PY
  chmod 644 "$T20/rpc.py"
  cat > "$T20/mint.py" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ.get("NYA02_T20", "/srv/nya02/t20"))
from rpc import call
SUBJECT = os.environ["NYA02_SUBJECT"]
W = sys.argv[1]
if W == "workspace":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": "op:t20:grant:workspace:%s" % SUBJECT,
        "capability": "PROVISION_PERSONAL_WORKSPACE", "scope": "workspace:personal",
        "target": "workspace:%s" % SUBJECT, "use_mode": "REUSABLE"}})
elif W == "artifact":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": "op:t20:grant:artifact:%s" % SUBJECT,
        "capability": "COMMIT_MANAGED_ARTIFACT", "scope": "artifact:personal",
        "target": sys.argv[2], "use_mode": "REUSABLE"}})
else:
    res = call({"op": "provision_workspace", "grant_id": sys.argv[2],
                "operation_key": "op:t20:workspace:%s" % SUBJECT,
                "workspace": {"subject_id": SUBJECT, "visibility": "PRIVATE",
                              "artifact_scope": "PERSONAL"}})
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.exit(0 if res.get("status") in {"ALLOW", "OK", "NO_CHANGE"} else 3)
PY
  chmod 755 "$T20/mint.py"
}

grants() {
  echo "== old operator issues the workspace grant"
  WSG_JSON=$(runuser -u nyaop -- env NYA02_SUBJECT="$SUBJECT" python3 "$T20/mint.py" workspace) || {
    echo "WORKSPACE_GRANT_REFUSED: $WSG_JSON"; return 4; }
  echo "   $WSG_JSON"
  WSG=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["grant_id"])' "$WSG_JSON")
  echo "== old service principal provisions the workspace"
  WS_JSON=$(runuser -u nyasvc -- env NYA02_SUBJECT="$SUBJECT" python3 "$T20/mint.py" provision "$WSG") || {
    echo "WORKSPACE_PROVISION_REFUSED: $WS_JSON"; return 5; }
  echo "   $WS_JSON"
  WS=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["workspace"]["workspace_id"])' "$WS_JSON")
  echo "== old operator issues the artifact grant for workspace:$WS"
  AG_JSON=$(runuser -u nyaop -- env NYA02_SUBJECT="$SUBJECT" python3 "$T20/mint.py" artifact "workspace:$WS") || {
    echo "ARTIFACT_GRANT_REFUSED: $AG_JSON"; return 6; }
  echo "   $AG_JSON"
  AG=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["grant_id"])' "$AG_JSON")
  echo "$WS" > "$T20/workspace_id"; echo "$AG" > "$T20/artifact_grant"; echo "$SUBJECT" > "$T20/subject"
  echo "workspace=$WS grant=$AG"
}

# The old owner's own operations (read_head / read_artifact / create_artifact /
# commit_markdown), spoken over its own socket, so the version history is the old
# owner's own, not a rewrite of its files.
artifact_through_old_owner() {
  echo "== old owner creates a real work with real version history"
  runuser -u nyasvc -- python3 - "$SOCK" "$SUBJECT" "$(cat "$T20/workspace_id")" \
      "$(cat "$T20/artifact_grant")" <<'PY'
import json, socket, sys
sock, subject, workspace, grant = sys.argv[1:5]


def call(req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(15.0); s.connect(sock)
    s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
    return json.loads(s.makefile("rb").readline(1_000_000))


def head_of(aid):
    # The old owner's own read op: read_head takes the artifact id (subject is optional).
    res = call({"op": "read_head", "artifact_id": aid})
    if res.get("status") != "OK" or not res.get("head"):
        raise SystemExit("ARTIFACT_HEAD_UNAVAILABLE: %s" % json.dumps(res, ensure_ascii=False))
    return res["head"]


created = call({"op": "create_artifact", "grant_id": grant,
                "operation_key": "op:t20:create:%s" % subject,
                "artifact": {"subject_id": subject, "workspace_id": workspace,
                             "title": "旧版手记", "content": "# 旧版手记\n\nv0.1 写的第一段。\n"}})
print("create:", json.dumps(created, ensure_ascii=False))
if created.get("status") not in ("OK", "NO_CHANGE") or not created.get("artifact_id"):
    raise SystemExit("ARTIFACT_CREATE_REFUSED: %s" % json.dumps(created, ensure_ascii=False))
aid = created["artifact_id"]
head = head_of(aid)["version_id"]
for n, text in ((2, "\n第二段：旧版补的。\n"), (3, "\n第三段：还是旧版补的。\n")):
    body = call({"op": "read_artifact", "subject": subject, "artifact_id": aid})
    if body.get("status") != "OK":
        raise SystemExit("ARTIFACT_READ_REFUSED: %s" % json.dumps(body, ensure_ascii=False))
    res = call({"op": "commit_markdown", "grant_id": grant,
                "operation_key": "op:t20:v%d:%s" % (n, subject),
                "artifact": {"subject_id": subject, "artifact_id": aid,
                             "expected_head": head,
                             "content": str(body["content"]) + text}})
    print("commit v%d:" % n, json.dumps(res, ensure_ascii=False))
    if res.get("status") not in ("OK", "NO_CHANGE"):
        raise SystemExit("ARTIFACT_COMMIT_REFUSED: %s" % json.dumps(res, ensure_ascii=False))
    head = head_of(aid)["version_id"]
print("artifact:", aid, "head:", head)
PY
}

build() {
  [ -d "$OLD" ] || { echo "BLOCKED: no old tree at $OLD (extract the tag first)"; return 3; }
  write_helpers
  start_old_owner fresh || return 1
  grants || return $?
  artifact_through_old_owner || return $?

  echo "== the OLD release's own setup_profile builds the v0.1 profile"
  # setup_profile refuses to overwrite an existing profile, so the harness profile is rebuilt
  # from nothing; the release's own refusal is what makes this explicit.
  rm -rf "$PROFILE"
  mkdir -p "$PROFILE"
  chown nyaop:nyaop "$PROFILE"
  runuser -u nyaop -- python3 "$OLD/scripts/setup_profile.py" \
    --home "$PROFILE" --owner t20owner --memory --life --allow-local-owner \
    --life-supply-socket "$SOCK" --life-supply-subject "$SUBJECT" \
    --life-supply-artifact-grant "$(cat "$T20/artifact_grant")" 2>&1 | tail -5

  echo "== the OLD plugin command path produces the chat session and the M0 memory"
  # HERMES_HOME is what the old plugin reads for <home>/chiyo/config.json; the builder speaks
  # only the old code (chiyo_bundle.hermes_plugin services()/chat()/memory_command()).
  runuser -u nyaop -- env HERMES_HOME="$PROFILE" NYA02_T20_OLD="$OLD" \
      python3 "$HERE/t20_build_old_state.py" > "$T20/build-old-state.log" 2>&1
  local rc=$?
  sed -n '1,200p' "$T20/build-old-state.log"
  [ $rc -eq 0 ] || { echo "OLD_STATE_BUILD_FAILED rc=$rc"; return 7; }
  python3 - "$T20/build-old-state.log" "$FACTS" <<'PY'
import json, sys
facts = None
for line in open(sys.argv[1], encoding='utf-8'):
    if line.startswith('T20_FACTS '):
        facts = json.loads(line[len('T20_FACTS '):])
if facts is None:
    raise SystemExit('OLD_STATE_BUILD_REPORTED_NO_FACTS')
open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(facts, ensure_ascii=False, indent=2))
print('facts written to', sys.argv[2])
PY
  [ $? -eq 0 ] || return 8

  echo "== what the old owner stored (its own database, read-only)"
  python3 - "$DATA/life_supply.sqlite" "$ARTIFACT_FACTS" "$SUBJECT" <<'PY'
import json, sqlite3, sys
db, out, subject = sys.argv[1:4]
con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
con.row_factory = sqlite3.Row
artifact = dict(con.execute("select artifact_id, workspace_id, title, lifecycle, revision,"
                            " head_version_id from managed_artifacts where subject_id=?",
                            (subject,)).fetchone())
versions = [dict(r) for r in con.execute(
    "select version_id, version_number, parent_version_id, content_sha256, operation_key"
    " from artifact_versions where artifact_id=? order by version_number", (artifact['artifact_id'],))]
grants = [dict(r) for r in con.execute(
    "select grant_id, subject, capability, scope, target, use_mode, revocation_state"
    " from permission_grants where subject=? order by created_at", (subject,))]
workspace = dict(con.execute("select workspace_id, subject_id, lifecycle from workspaces"
                             " where subject_id=?", (subject,)).fetchone())
snapshot = {"subject": subject, "workspace": workspace, "artifact": artifact,
            "versions": versions, "grants": grants}
open(out, 'w', encoding='utf-8').write(json.dumps(snapshot, ensure_ascii=False, indent=2))
print(json.dumps(snapshot, ensure_ascii=False, indent=2))
PY
  [ $? -eq 0 ] || return 9

  echo "== what the old code produced"
  find "$PROFILE" -maxdepth 3 | sort | head -40
  return 0
}

case "${1:-build}" in
  build) build ;;
  verify) bash "$HERE/t20_verify.sh" ;;
  owner-start) start_old_owner keep ;;
  stop) stop_old_owner; echo "stopped" ;;
  *) echo "usage: $0 build|verify|owner-start|stop"; exit 2;;
esac
