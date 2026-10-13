#!/usr/bin/env bash
# v0.2 isolated acceptance environment for the autonomous life cycle.
#
# Creates two real non-root identities, an isolated Life Supply owner on a Unix socket, the
# two Governance grants the owner requires, and then runs `components/check_life_v02.py`.
#
# Usage (as root, on Linux; the Life runtime needs POSIX fcntl):
#     bash components/life/tests/v02/run_isolated_acceptance.sh
#     NYA02_ROOT=/srv/nya02 bash components/life/tests/v02/run_isolated_acceptance.sh
#
# Everything it creates lives under $NYA02_ROOT and is deleted at the start of a run, so the
# owner starts empty -- which is what makes the first assertion (an empty owner yields no
# activity) meaningful.  It never touches a real profile, a real ~/.nyairo or a live service.
set -uo pipefail

ROOT="${NYA02_ROOT:-/srv/nya02}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SUBJECT="${NYA02_SUBJECT:-chiyo}"
OP_USER="${NYA02_OP_USER:-nyaop}"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"

echo "== repository: $REPO"
echo "== root:       $ROOT"
echo "== subject:    $SUBJECT"

if [ "$(id -u)" != "0" ]; then
  echo "this harness needs root to create the two identities and to start the owner" >&2
  exit 2
fi

# ---- 1. two real non-root identities ---------------------------------------------------
id "$OP_USER"  >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin "$OP_USER"
id "$SVC_USER" >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin "$SVC_USER"
OP_UID="$(id -u "$OP_USER")"
SVC_UID="$(id -u "$SVC_USER")"
echo "== operator uid $OP_UID / service uid $SVC_UID"

# ---- 2. isolated data root + owner process --------------------------------------------
pkill -f 'supply_service.py' 2>/dev/null || true
sleep 0.5
rm -rf "$ROOT"
mkdir -p "$ROOT/data" "$ROOT/sock" "$ROOT/life-state"
chmod 755 "$ROOT" "$ROOT/sock"
chmod 700 "$ROOT/life-state"
chown -R "$SVC_USER:$SVC_USER" "$ROOT/life-state"

export LIFE_SUPPLY_SOCKET="$ROOT/sock/life-supply.sock"
export LIFE_SUPPLY_DATA_ROOT="$ROOT/data"
export LIFE_SUPPLY_OPERATOR_UIDS="$OP_UID"
export LIFE_SUPPLY_SERVICE_UIDS="$SVC_UID"
# The owner gates each write on its own switch; the harness turns on only what it needs.
export LIFE_SUPPLY_SWITCH_WORKSPACE_PROVISION=ON
export LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION=ON

cd "$REPO"
nohup python3 scripts/supply_service.py > "$ROOT/service.log" 2>&1 &
echo $! > "$ROOT/service.pid"
for _ in $(seq 1 60); do
  [ -S "$LIFE_SUPPLY_SOCKET" ] && break
  sleep 0.25
done
if [ ! -S "$LIFE_SUPPLY_SOCKET" ]; then
  echo "owner did not come up:" >&2
  tail -30 "$ROOT/service.log" >&2
  exit 3
fi
echo "== owner started: $LIFE_SUPPLY_SOCKET"

# The owner creates its socket 0600 on purpose, which is the arrangement recorded in
# KNOWN_ISSUES as blocking two different ordinary identities from connecting.  The harness
# relaxes only the *file* mode so both identities can reach the socket; the owner's
# authorisation decision is still made from SO_PEERCRED, so operator/service separation is
# genuinely exercised rather than bypassed.
chmod 0666 "$LIFE_SUPPLY_SOCKET"

# ---- 3. the code the service identity will execute ------------------------------------
mkdir -p "$ROOT/app"
cp -r "$REPO/components" "$ROOT/app/components"
chmod -R a+rX "$ROOT/app"

cat > "$ROOT/rpc.py" <<'PY'
"""One-line JSON RPC over the Life Supply Unix socket.  Harness helper."""
import json
import os
import socket
import sys


def call(req, socket_path=None, timeout=10.0):
    path = socket_path or os.environ["LIFE_SUPPLY_SOCKET"]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
    return json.loads(s.makefile("rb").readline(1_000_000))


if __name__ == "__main__":
    print(json.dumps(call(json.loads(sys.argv[1])), ensure_ascii=False, sort_keys=True))
PY
chmod 644 "$ROOT/rpc.py"

# ---- 4. Governance: operator mints, service principal acts ----------------------------
svc() {
  runuser -u "$SVC_USER" -- env LIFE_SUPPLY_SOCKET="$LIFE_SUPPLY_SOCKET" "$@"
}
op() {
  runuser -u "$OP_USER" -- env LIFE_SUPPLY_SOCKET="$LIFE_SUPPLY_SOCKET" "$@"
}

export NYA02_HELPER_DIR="$ROOT"
export NYA02_SUBJECT="$SUBJECT"

issue_grant() {  # capability scope target operation_id
  op python3 -c '
import json, os, sys
sys.path.insert(0, os.environ["NYA02_HELPER_DIR"])
from rpc import call
cap, scope, target, op_id = sys.argv[1:5]
res = call({"op": "issue_grant", "grant": {
    "subject": os.environ["NYA02_SUBJECT"], "operation_id": op_id,
    "capability": cap, "scope": scope, "target": target, "use_mode": "REUSABLE"}})
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.exit(0 if res.get("status") in {"ALLOW", "NO_CHANGE"} else 3)
' "$1" "$2" "$3" "$4"
}

echo "== operator issues the workspace grant"
WS_GRANT_JSON="$(issue_grant PROVISION_PERSONAL_WORKSPACE workspace:personal "workspace:$SUBJECT" op:grant:workspace:1)"
echo "   $WS_GRANT_JSON"
WS_GRANT="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["grant_id"])' "$WS_GRANT_JSON")"

echo "== service principal provisions the workspace"
WS_JSON="$(svc python3 -c '
import json, os, sys
sys.path.insert(0, os.environ["NYA02_HELPER_DIR"])
from rpc import call
res = call({"op": "provision_workspace", "grant_id": sys.argv[1],
            "operation_key": "op:workspace:provision:1",
            "workspace": {"subject_id": os.environ["NYA02_SUBJECT"],
                          "visibility": "PRIVATE", "artifact_scope": "PERSONAL"}})
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.exit(0 if res.get("status") in {"OK", "NO_CHANGE"} else 3)
' "$WS_GRANT")"
echo "   $WS_JSON"
WS_ID="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["workspace"]["workspace_id"])' "$WS_JSON")"

echo "== operator issues the artifact grant for target workspace:$WS_ID"
ART_GRANT_JSON="$(issue_grant COMMIT_MANAGED_ARTIFACT artifact:personal "workspace:$WS_ID" op:grant:artifact:1)"
echo "   $ART_GRANT_JSON"
ART_GRANT="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["grant_id"])' "$ART_GRANT_JSON")"

# ---- 5. the acceptance check itself ---------------------------------------------------
echo
echo "== running components/check_life_v02.py as $SVC_USER"
svc env \
  NYA02_SUPPLY_SOCKET="$LIFE_SUPPLY_SOCKET" \
  NYA02_WORKSPACE_ID="$WS_ID" \
  NYA02_ARTIFACT_GRANT="$ART_GRANT" \
  NYA02_LIFE_STATE="$ROOT/life-state" \
  python3 "$ROOT/app/components/check_life_v02.py"
RC=$?

echo
echo "== owner state after the run"
python3 - "$ROOT" <<'PY'
import json, pathlib, sqlite3, sys
root = pathlib.Path(sys.argv[1])
con = sqlite3.connect(f"file:{root / 'data' / 'life_supply.sqlite'}?mode=ro", uri=True)
print("managed_artifacts:")
for r in con.execute("select artifact_id, title, head_version_id, revision, lifecycle "
                     "from managed_artifacts"):
    print("  ", r)
print("artifact_versions:")
for r in con.execute("select version_number, version_id, parent_version_id, length(content), "
                     "operation_key from artifact_versions order by version_number"):
    print("   v%s id=%s parent=%s chars=%s op=%s" % r)
con.close()
journal = root / "life-state" / "life_step_journal.jsonl"
print("step journal:")
if journal.exists():
    for line in journal.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        print("  ", {k: d.get(k) for k in ("effect_state", "status", "version_number",
                                           "expected_head", "operation_key", "activity_ref")})
else:
    print("   (none)")
PY

echo
if [ "$RC" -eq 0 ]; then
  echo "ISOLATED ACCEPTANCE: PASS"
else
  echo "ISOLATED ACCEPTANCE: FAIL (rc=$RC)"
fi
exit "$RC"
