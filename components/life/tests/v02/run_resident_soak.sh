#!/usr/bin/env bash
# B8-6: prepare the dedicated soak profile for the resident.
#
# The profile is deliberately separate from anything production: its own subject, its own
# workspace and artifact grant, its own state root, its own Memory database, its own log.  It
# never points at the live instance, and it never touches another subject's work.
#
#   run_resident_soak.sh prepare     # mint the profile and create the first work
#   run_resident_soak.sh world-env   # print the World environment this profile would use
#
# Preparing twice is safe: an existing grant is kept, so a restart continues the same work
# instead of minting a second one.
set -uo pipefail

ROOT="${NYA02_ROOT:-/srv/nya02}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
OP_USER="${NYA02_OP_USER:-nyaop}"
RESIDENT_DIR="${NYA02_RESIDENT_DIR:-$ROOT/resident}"
SUBJECT="${NYA02_RESIDENT_SUBJECT:-chiyo-soak}"
WORLD_HOME="${NYA02_WORLD_INSTANCE:-/srv/nya02-world}"
WORLD_ENV_FILE="${NYA02_WORLD_ENV_FILE:-$WORLD_HOME/env.sh}"

[ -f "$ROOT/env.sh" ] && source "$ROOT/env.sh"
SOCKET="${NYA02_SUPPLY_SOCKET:-${LIFE_SUPPLY_SOCKET:-$ROOT/sock/life-supply.sock}}"

# The World's own names, exported only when a World instance is actually there.  Nothing from
# the World instance's env file is sourced wholesale: it also carries Life settings that would
# silently override this profile's own.
world_env() {
  if [ ! -f "$WORLD_ENV_FILE" ]; then
    echo "# no World instance env at $WORLD_ENV_FILE: the World feed will be UNAVAILABLE"
    return 0
  fi
  python3 - "$WORLD_ENV_FILE" <<'PY'
import re, sys

wanted = (
    "WORLD_BODY_INTEGRATION_MODE", "WORLD_BODY_SOCKET", "WORLD_BODY_INTENT_MUTATION_GATE",
    "CHIYO_WORLD_HOME", "CHIYO_WORLD_RUNTIME", "CHIYO_WORLD_ID",
    "WORLD_ACTION_AUTH_REGISTRY_DIR", "WORLD_ACTION_LIFECYCLE_PATH",
    "BODY_RUNTIME_MODE", "CHIYO_WORLD_BODY_AUTHORITY", "CHIYO_BODY_READER_MODE",
    "CHIYO_BODY_RUNTIME_ENABLED", "CHIYO_WORLD_BODY_SUBSTRATE_ENABLED",
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED", "NYA02_WORLD_HOME",
)
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line.startswith("export "):
        continue
    body = line[len("export "):]
    name, _, value = body.partition("=")
    if name.strip() in wanted:
        print("export %s=%s" % (name.strip(), value.strip()))
PY
}

case "${1:-prepare}" in
  world-env) world_env; exit 0;;
  prepare) ;;
  *) echo "usage: $0 prepare|world-env"; exit 2;;
esac

[ -S "$SOCKET" ] || { echo "BLOCKED: NO_SUPPLY_OWNER at $SOCKET"; exit 3; }
mkdir -p "$RESIDENT_DIR" "$ROOT/tools"
# The service identity owns its own root and must be able to traverse it; the operator identity
# runs the mint tool, so that tool lives in the shared tools directory instead.
chown "$SVC_USER:$SVC_USER" "$RESIDENT_DIR"
chmod 700 "$RESIDENT_DIR"

if [ -f "$RESIDENT_DIR/artifact_grant" ]; then
  echo "profile already prepared: subject=$SUBJECT grant=$(cat "$RESIDENT_DIR/artifact_grant")"
else
  cat > "$ROOT/tools/soak_mint.py" <<'PY'
"""One grant tool for the soak profile, with operation ids of its own."""
import json, os, sys
sys.path.insert(0, "/srv/nya02/tools")
from rpc import call

SUBJECT = os.environ["NYA02_SUBJECT"]
WHICH = sys.argv[1]

if WHICH == "workspace":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": "op:soak:grant:workspace:%s" % SUBJECT,
        "capability": "PROVISION_PERSONAL_WORKSPACE", "scope": "workspace:personal",
        "target": "workspace:%s" % SUBJECT, "use_mode": "REUSABLE"}})
elif WHICH == "artifact":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": "op:soak:grant:artifact:%s" % SUBJECT,
        "capability": "COMMIT_MANAGED_ARTIFACT", "scope": "artifact:personal",
        "target": sys.argv[2], "use_mode": "REUSABLE"}})
else:
    res = call({"op": "provision_workspace", "grant_id": sys.argv[2],
                "operation_key": "op:soak:workspace:provision:%s" % SUBJECT,
                "workspace": {"subject_id": SUBJECT, "visibility": "PRIVATE",
                              "artifact_scope": "PERSONAL"}})
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.exit(0 if res.get("status") in {"ALLOW", "OK", "NO_CHANGE"} else 3)
PY
  chmod 755 "$ROOT/tools/soak_mint.py"

  echo "== operator issues the workspace grant"
  WSG=$(runuser -u "$OP_USER" -- env NYA02_SUBJECT="$SUBJECT" \
        python3 "$ROOT/tools/soak_mint.py" workspace | python3 -c \
        "import json,sys;print(json.load(sys.stdin)['grant_id'])")
  echo "== service principal provisions the workspace"
  WS=$(runuser -u "$SVC_USER" -- env NYA02_SUBJECT="$SUBJECT" \
       python3 "$ROOT/tools/soak_mint.py" provision "$WSG" | python3 -c \
       "import json,sys;print((json.load(sys.stdin).get('workspace') or {})['workspace_id'])")
  echo "== operator issues the artifact grant for workspace:$WS"
  AG=$(runuser -u "$OP_USER" -- env NYA02_SUBJECT="$SUBJECT" \
       python3 "$ROOT/tools/soak_mint.py" artifact "workspace:$WS" | python3 -c \
       "import json,sys;print(json.load(sys.stdin)['grant_id'])")
  echo "$WS" > "$RESIDENT_DIR/workspace_id"
  echo "$AG" > "$RESIDENT_DIR/artifact_grant"
  echo "workspace=$WS grant=$AG"
fi

# The profile's own subject, recorded so a restart cannot drift onto another subject's work.
echo "$SUBJECT" > "$RESIDENT_DIR/subject"

WS="$(cat "$RESIDENT_DIR/workspace_id")"
AG="$(cat "$RESIDENT_DIR/artifact_grant")"
MEMORY_DIR="${NYA02_RESIDENT_MEMORY_DIR:-$RESIDENT_DIR/memory}"
mkdir -p "$MEMORY_DIR"
chown -R "$SVC_USER:$SVC_USER" "$MEMORY_DIR"
chmod 700 "$MEMORY_DIR"

echo "== the first real work, created by a legal owner operation"
runuser -u "$SVC_USER" -- env PYTHONPATH="$REPO/components/life/scripts:$REPO/components/native:$REPO/components/native/src:$REPO/components/native/memory_runtime_v1" \
  python3 - "$SOCKET" "$SUBJECT" "$WS" "$AG" "$MEMORY_DIR" <<'PY'
import sys

socket_path, subject, workspace, grant, memory_dir = sys.argv[1:6]

from app.evidence import EvidenceStore
from life_artifact_port import LifeArtifactPort
from life_experience_port import life_conversation_id
from memory_control import MemoryControlLedger

evidence_db = "%s/evidence.db" % memory_dir
controls_db = "%s/controls.db" % memory_dir
EvidenceStore(evidence_db)
MemoryControlLedger(controls_db, evidence_db, owner=subject,
                    conversation=life_conversation_id(subject)).initialize()
print("memory ready: %s (controls %s)" % (evidence_db, controls_db))

port = LifeArtifactPort(socket_path, subject)
existing = port.latest_resumable()
if existing is not None:
    print("work already present: %s head=%s v%s"
          % (existing.artifact_id, existing.head_version_id, existing.version_number))
else:
    created = port.create_artifact(
        workspace_id=workspace, title="长测的手记",
        content="# 长测的手记\n\n第一段：先写一句，看看她会不会自己接着写。\n",
        grant_id=grant, operation_key="op:soak:create:%s" % subject)
    print("created: %s status=%s reason=%s" % (created.artifact_id, created.status, created.reason))
    head = port.read_head(created.artifact_id) or {}
    print("head=%s v%s" % (head.get("version_id"), head.get("version_number")))
PY

echo
echo "== soak profile ready"
echo "subject      : $SUBJECT"
echo "state root   : ${NYA02_RESIDENT_STATE:-$RESIDENT_DIR/state}"
echo "memory db    : $MEMORY_DIR/evidence.db"
echo "workspace    : $WS"
echo "grant        : $AG"
echo "world env    : $(world_env | tr '\n' ' ')"
echo
echo "start it with:  NYA02_RESIDENT_POLL_SECONDS=180 bash $0 2>/dev/null; bash $(dirname "$0")/run_resident.sh start"
