#!/usr/bin/env bash
# B6-1 / B6-4 / B6-5: an isolated World/Body instance and one real MOVE, end to end.
#
# What it builds (all under $NYA02_WORLD_ROOT, deleted at the start of a run):
#
#   world/        the World home, initialised by the real initialiser
#   life-state/   the isolated Life state root
#   env.sh        the switches both processes read
#
# Identity (B6-4): the gateway surface admits exactly the World service's own
# euid (world_body_service.GATEWAY_ALLOWED_PEER_UID = os.geteuid()).  The Life
# client is therefore run as the *same* uid as the service -- the peer is aligned
# rather than the socket permission being loosened or the check being removed.
#
# Usage (as root, on Linux; the World runtime needs POSIX fcntl and AF_UNIX):
#     bash components/life/tests/v02/run_world_move_e2e.sh
#     NYA02_WORLD_ROOT=/srv/nya02-world bash components/life/tests/v02/run_world_move_e2e.sh
set -uo pipefail

ROOT="${NYA02_WORLD_ROOT:-/srv/nya02-world}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SVC_USER="${NYA02_WORLD_SVC_USER:-nyasvc}"
DRIVER="$REPO/components/life/tests/v02/world_move_e2e.py"

echo "== repository: $REPO"
echo "== root:       $ROOT"
echo "== identity:   $SVC_USER"

if [ "$(id -u)" != "0" ]; then
  echo "this harness needs root to create the service identity and to own the instance" >&2
  exit 2
fi
id "$SVC_USER" >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin "$SVC_USER"

stop_service() {
  if [ -f "$ROOT/world-service.pid" ]; then
    kill "$(cat "$ROOT/world-service.pid")" 2>/dev/null || true
  fi
  for _ in $(seq 1 40); do
    [ -S "$ROOT/world/run/gateway.sock" ] || break
    sleep 0.25
  done
  pkill -f 'world_body_service.py' 2>/dev/null || true
}

start_service() {
  runuser -u "$SVC_USER" -- bash -c "set -a; . '$ROOT/env.sh'; set +a; exec python3 \
      '$REPO/components/world/service/world_body_service.py' run \
      --runtime-root '$REPO/components/world' --world-home '$ROOT/world' --tick-seconds 1" \
      > "$ROOT/world-service.log" 2>&1 &
  echo $! > "$ROOT/world-service.pid"
  for _ in $(seq 1 80); do
    [ -S "$ROOT/world/run/gateway.sock" ] && return 0
    sleep 0.25
  done
  echo "the World service did not come up:" >&2
  tail -40 "$ROOT/world-service.log" >&2
  return 1
}

init_instance() {
  rm -rf "$ROOT/world"
  python3 "$REPO/chiyo_bundle/world_read_service.py" init --home "$ROOT/world" || return 1
  python3 - "$ROOT/world/data/world/world_state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print("   initialised: timezone=%s world_id=%s place=%s revision=%s"
      % (state["timezone"], state["world_id"], state["location"]["place_id"], state["revision"]))
assert state["timezone"] == "Asia/Tokyo", "the instance is not in the skeleton's timezone"
PY
  # Seed only absent embodiment, through its canonical writer -- exactly what the
  # read-only World port does.  Without a substrate the snapshot carries no pose,
  # and no action space can be built at all.
  python3 - "$REPO" "$ROOT/world" <<'PY'
import sys
repo, home = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo + "/components/world/service")
sys.path.insert(0, repo + "/components/world/scripts")
import world_body_substrate as substrate
store = substrate.SubstrateStore(substrate.substrate_path(home))
if store.load() is None:
    store.save(substrate.initial_substrate(world_id="shiomi_city"))
    print("   seeded the embodiment substrate")
else:
    print("   embodiment substrate already present")
PY
  mkdir -p "$ROOT/world/run"
  chown -R "$SVC_USER:$SVC_USER" "$ROOT/world"
}

as_service() {  # run one command as the service identity, with the instance env
  runuser -u "$SVC_USER" -- bash -c "set -a; . '$ROOT/env.sh'; set +a; exec \"\$@\"" _ "$@"
}

# ---- 1. a clean instance ---------------------------------------------------------------
stop_service
rm -rf "$ROOT"
mkdir -p "$ROOT/life-state"
chmod 700 "$ROOT/life-state"

cat > "$ROOT/env.sh" <<EOF
export WORLD_BODY_INTEGRATION_MODE=canonical
export WORLD_BODY_SOCKET=$ROOT/world/run/gateway.sock
export WORLD_BODY_INTENT_MUTATION_GATE=canonical
export CHIYO_WORLD_HOME=$ROOT/world
export CHIYO_WORLD_RUNTIME=$REPO/components/world
export CHIYO_WORLD_ID=shiomi_city
export WORLD_ACTION_AUTH_REGISTRY_DIR=$ROOT/world/run
export WORLD_ACTION_LIFECYCLE_PATH=$ROOT/world/run/action_lifecycle.jsonl
# The World service's own gates.  Canonical needs all of them open: the runtime
# mode, the authority, the reader mode, the embodiment substrate and the World
# engine itself (world_body_config:37-43).
export BODY_RUNTIME_MODE=canonical
export CHIYO_WORLD_BODY_AUTHORITY=production
export CHIYO_BODY_READER_MODE=canonical
export CHIYO_BODY_RUNTIME_ENABLED=1
export CHIYO_WORLD_BODY_SUBSTRATE_ENABLED=1
export CHIYO_WORLD_LIVING_SKELETON_ENABLED=1
export NYAIRO_WORLD_BRIDGE_DIR=$REPO/components/world/service/bridge
export NYA02_WORLD_HOME=$ROOT/world
export NYA02_WORLD_LIFE_STATE=$ROOT/life-state
export CHIYO_LIFE_INSTALL_ROOT=$ROOT/life-state
export CHIYO_LIFE_PRODUCTION_CUTOVER=canonical
export LIFE_RUNTIME_ENABLED=true
export AGENCY_ENABLED=true
export CHIYO_AUTONOMOUS_LIFE_ENABLED=true
export CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY=16
EOF
chmod 644 "$ROOT/env.sh"
chown -R "$SVC_USER:$SVC_USER" "$ROOT/life-state"

echo "== B6-1: initialising an isolated World instance"
init_instance || { echo "WORLD_MOVE_E2E: FAIL (initialisation)"; exit 3; }

RC_MOVE=1; RC_VERIFY=1; RC_FAULTS=1

# ---- 2. the real MOVE ----------------------------------------------------------------
echo
echo "== B6-5: starting the World owner"
if start_service; then
  as_service python3 "$DRIVER" move
  RC_MOVE=$?

  echo
  echo "== B6-5: restarting the World owner"
  stop_service
  if start_service; then
    as_service python3 "$DRIVER" verify
    RC_VERIFY=$?
  fi
  stop_service
fi

# ---- 3. rejection and reconciliation, on a fresh instance ----------------------------
echo
echo "== B6-5: a fresh instance for the rejection / reconciliation cases"
init_instance || { echo "WORLD_MOVE_E2E: FAIL (re-initialisation)"; exit 3; }
# A fresh Life state root as well: the previous phase left a foreground Activity, and
# with one present the only legal move is to continue it -- which would hide the cases
# this phase is actually about.
rm -rf "$ROOT/life-state"
mkdir -p "$ROOT/life-state"
chmod 700 "$ROOT/life-state"
chown -R "$SVC_USER:$SVC_USER" "$ROOT/life-state"
if start_service; then
  as_service python3 "$DRIVER" faults
  RC_FAULTS=$?
fi
stop_service

echo
echo "== final World state =="
python3 - "$ROOT/world/data/world/world_state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print("   place=%s revision=%s timezone=%s"
      % (state["location"]["place_id"], state["revision"], state["timezone"]))
PY

echo
if [ "$RC_MOVE" -eq 0 ] && [ "$RC_VERIFY" -eq 0 ] && [ "$RC_FAULTS" -eq 0 ]; then
  echo "WORLD_MOVE_ACCEPTANCE: PASS"
  exit 0
fi
echo "WORLD_MOVE_ACCEPTANCE: FAIL (move=$RC_MOVE verify=$RC_VERIFY faults=$RC_FAULTS)"
exit 1
