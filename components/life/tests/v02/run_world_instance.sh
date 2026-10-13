#!/usr/bin/env bash
# B8-6 helper: bring the isolated World instance up, so the resident's World feed is real.
#
#   run_world_instance.sh start|stop|status
#
# This is the same instance `run_world_move_e2e.sh` builds (its own home, its own gateway socket,
# its own env file); it is separated out so a long-running soak can have the World without also
# running the move acceptance scenario.  The service must run as the same identity the Life side
# uses: the gateway only admits its own euid (world_body_service.GATEWAY_ALLOWED_PEER_UID).
set -uo pipefail

W="${NYA02_WORLD_INSTANCE:-/srv/nya02-world}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
ENV_FILE="$W/env.sh"
LOG="$W/world-service.log"
PIDFILE="$W/world-service.pid"
SOCKET="$W/world/run/gateway.sock"

pids() { pgrep -f "world_body_service.py run" 2>/dev/null || true; }

write_env() {
  cat > "$ENV_FILE" <<EOF
export WORLD_BODY_INTEGRATION_MODE=canonical
export WORLD_BODY_SOCKET=$SOCKET
export WORLD_BODY_INTENT_MUTATION_GATE=canonical
export CHIYO_WORLD_HOME=$W/world
export CHIYO_WORLD_RUNTIME=$REPO/components/world
export CHIYO_WORLD_ID=shiomi_city
export WORLD_ACTION_AUTH_REGISTRY_DIR=$W/world/run
export WORLD_ACTION_LIFECYCLE_PATH=$W/world/run/action_lifecycle.jsonl
# Canonical needs every gate open: runtime mode, authority, reader mode, the embodiment
# substrate and the World engine itself (world_body_config:37-43).
export BODY_RUNTIME_MODE=canonical
export CHIYO_WORLD_BODY_AUTHORITY=production
export CHIYO_BODY_READER_MODE=canonical
export CHIYO_BODY_RUNTIME_ENABLED=1
export CHIYO_WORLD_BODY_SUBSTRATE_ENABLED=1
export CHIYO_WORLD_LIVING_SKELETON_ENABLED=1
export NYAIRO_WORLD_BRIDGE_DIR=$REPO/components/world/service/bridge
export NYA02_WORLD_HOME=$W/world
export NYA02_WORLD_LIFE_STATE=$W/life-state
EOF
}

init_instance() {
  rm -rf "$W/world"
  python3 "$REPO/chiyo_bundle/world_read_service.py" init --home "$W/world" || return 1
  python3 - "$W/world/data/world/world_state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print("   initialised: timezone=%s world_id=%s place=%s revision=%s"
      % (state["timezone"], state["world_id"], state["location"]["place_id"], state["revision"]))
assert state["timezone"] == "Asia/Tokyo", "the instance is not in the skeleton's timezone"
PY
  # Seed only absent embodiment, through its canonical writer: without a substrate the snapshot
  # carries no pose, and no action space can be built at all.
  python3 - "$REPO" "$W/world" <<'PY'
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
  mkdir -p "$W/world/run"
  chown -R "$SVC_USER:$SVC_USER" "$W/world"
}

start_service() {
  [ -n "$(pids)" ] && { echo "ALREADY_RUNNING pids=$(pids)"; return 0; }
  mkdir -p "$W/life-state"
  chmod 700 "$W/life-state"
  chown -R "$SVC_USER:$SVC_USER" "$W/life-state"
  write_env
  # Detached on purpose: a service started as a plain background child of this script dies with
  # SIGHUP the moment the script exits, and then every World read is a connection refusal.
  nohup setsid runuser -u "$SVC_USER" -- bash -c "set -a; . '$ENV_FILE'; set +a; exec python3 \
'$REPO/components/world/service/world_body_service.py' run" > "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 60); do
    [ -S "$SOCKET" ] && break
    sleep 0.5
  done
  if [ ! -S "$SOCKET" ]; then
    echo "WORLD_GATEWAY_NOT_CREATED"; tail -25 "$LOG"; return 1
  fi
  echo "STARTED pids=$(pids) socket=$SOCKET"
}

stop_service() {
  [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
  pkill -f "world_body_service.py run" 2>/dev/null || true
  sleep 1
  [ -z "$(pids)" ] && echo "STOPPED" || echo "STILL_RUNNING pids=$(pids)"
}

status_service() {
  local running
  running="$(pids)"
  echo "pids=${running:-none}"
  [ -S "$SOCKET" ] && echo "socket=present" || echo "socket=absent"
  [ -f "$LOG" ] && tail -3 "$LOG"
}

case "${1:-status}" in
  start) init_instance || exit 4; start_service;;
  stop) stop_service;;
  status) status_service;;
  *) echo "usage: $0 start|stop|status"; exit 2;;
esac
