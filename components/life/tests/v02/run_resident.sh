#!/usr/bin/env bash
# B8-6 launcher: run the autonomous-life resident against exactly one state root.
#
#   run_resident.sh start|stop|status|foreground [extra args for the resident]
#
# The isolated environment already manages its long-lived processes with nohup + a pidfile (the
# Life Supply owner and the World service both do), so this follows that mechanism rather than
# introducing a scheduler.  A systemd unit is documented in the batch record for a real
# deployment; systemd is `offline` inside this WSL instance, so it is not what is verified here.
#
# The provider credentials live in the operator's profile (0600 under /root), so this resolves
# them as root and hands them to the service identity through the process environment only.
# Nothing is copied into the repository and no key is printed.
set -uo pipefail

ROOT="${NYA02_ROOT:-/srv/nya02}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
PROFILE="${NYA02_PROFILE_HOME:-/root/.nyairo}"

RESIDENT_DIR="${NYA02_RESIDENT_DIR:-$ROOT/resident}"
STATE_ROOT="${NYA02_RESIDENT_STATE:-$RESIDENT_DIR/state}"
LOG="${NYA02_RESIDENT_LOG:-$RESIDENT_DIR/resident.log}"
PIDFILE="${NYA02_RESIDENT_PID:-$RESIDENT_DIR/resident.pid}"
COMMITFILE="${NYA02_RESIDENT_COMMIT:-$RESIDENT_DIR/CODE_COMMIT}"
APP_DIR="${NYA02_RESIDENT_APP:-$RESIDENT_DIR/app}"
POLL="${NYA02_RESIDENT_POLL_SECONDS:-180}"

[ -f "$ROOT/env.sh" ] && source "$ROOT/env.sh"
SOCKET="${NYA02_SUPPLY_SOCKET:-${LIFE_SUPPLY_SOCKET:-$ROOT/sock/life-supply.sock}}"
SUBJECT="${NYA02_RESIDENT_SUBJECT:-${NYA02_SUBJECT:-chiyo}}"

# The prepared profile names its own subject, and that name wins: sourcing the environment above
# must not be able to drift a restart onto a different subject's work.
if [ -f "$RESIDENT_DIR/subject" ]; then SUBJECT="$(cat "$RESIDENT_DIR/subject")"; fi

# The World's own configuration, when a World instance is there.  Only the World names are
# taken: the helper filters them, so the World instance's Life settings cannot override this
# profile's own.  With no World instance the feed is recorded UNAVAILABLE, which is allowed.
WORLD_ENV_HELPER="${NYA02_WORLD_ENV_HELPER:-$(dirname "$0")/run_resident_soak.sh}"
if [ -f "$WORLD_ENV_HELPER" ]; then
  eval "$(bash "$WORLD_ENV_HELPER" world-env 2>/dev/null | grep '^export ' || true)"
fi
# The explicit `env` list below replaces the whole environment, so the World names have to be
# carried across by name.  Without this the resident would run without them, see no World
# socket, and record the feed UNAVAILABLE -- which is exactly the bug this line fixes.
PASSTHROUGH_VARS="WORLD_BODY_INTEGRATION_MODE WORLD_BODY_SOCKET WORLD_BODY_INTENT_MUTATION_GATE \
CHIYO_WORLD_HOME CHIYO_WORLD_RUNTIME CHIYO_WORLD_ID WORLD_ACTION_AUTH_REGISTRY_DIR \
WORLD_ACTION_LIFECYCLE_PATH BODY_RUNTIME_MODE CHIYO_WORLD_BODY_AUTHORITY CHIYO_BODY_READER_MODE \
CHIYO_BODY_RUNTIME_ENABLED CHIYO_WORLD_BODY_SUBSTRATE_ENABLED \
CHIYO_WORLD_LIVING_SKELETON_ENABLED NYAIRO_WORLD_BRIDGE_DIR NYA02_WORLD_HOME \
CHIYO_AUTONOMOUS_LIFE_ENABLED CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY \
CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY \
CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"
passthrough_env_args=()
for _name in $PASSTHROUGH_VARS; do
  if [ -n "${!_name:-}" ]; then passthrough_env_args+=("$_name=${!_name}"); fi
done

pids() {
  pgrep -f "autonomous_life_resident.py --state-root $STATE_ROOT" 2>/dev/null || true
}

resolve_model_env() {
  eval "$(python3 - "$PROFILE" <<'PY'
import os, re, shlex, sys

home = sys.argv[1]


def read_env(path):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


text = ""
try:
    text = open(os.path.join(home, "config.yaml"), encoding="utf-8").read()
except OSError:
    pass


def scalar(key, indent=2):
    m = re.search(rf"^{' ' * indent}{key}:\s*(\S.*?)\s*$", text, re.M)
    return m.group(1).strip() if m else ""


provider = scalar("provider") or "custom"
model = scalar("default")
base_url = scalar("base_url")
api_key = scalar("api_key")
env_file = read_env(os.path.join(home, ".env"))
_ref = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", api_key or "")
if _ref:
    api_key = os.environ.get(_ref.group(1)) or env_file.get(_ref.group(1), "")
if not api_key:
    blk = re.search(r"^custom_providers:\s*$(.*?)(?=^\S|\Z)", text, re.M | re.S)
    if blk:
        pb = re.search(rf"^  {re.escape(provider)}:\s*$(.*?)(?=^  \S|\Z)",
                       blk.group(1), re.M | re.S)
        if pb:
            body = pb.group(1)
            m = re.search(r"^\s*key_env:\s*(\S+)\s*$", body, re.M)
            if m:
                api_key = os.environ.get(m.group(1)) or env_file.get(m.group(1), "")
            m = re.search(r"^\s*base_url:\s*(\S+)\s*$", body, re.M)
            if m and not base_url:
                base_url = m.group(1)
            m = re.search(r"^\s*model:\s*(\S+)\s*$", body, re.M)
            if m and not model:
                model = m.group(1)

for name, value in (("NYA02_MODEL_PROVIDER", provider), ("NYA02_MODEL_NAME", model),
                    ("NYA02_MODEL_BASE_URL", base_url), ("NYA02_MODEL_API_KEY", api_key)):
    print(f"export {name}={shlex.quote(value or '')}")
PY
)"
}

prepare_code() {
  # Freeze the code the resident executes, and record which commit that was.
  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  cp -r "$REPO/components" "$APP_DIR/components"
  cp -r "$REPO/chiyo_bundle" "$APP_DIR/chiyo_bundle" 2>/dev/null || true
  chmod -R a+rX "$APP_DIR"
  find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
  ( cd "$REPO" && git rev-parse HEAD ) > "$COMMITFILE" 2>/dev/null || echo unknown > "$COMMITFILE"
}

prepare_state() {
  mkdir -p "$RESIDENT_DIR" "$STATE_ROOT"
  chown -R "$SVC_USER:$SVC_USER" "$RESIDENT_DIR"
  chmod 700 "$STATE_ROOT" "$RESIDENT_DIR"
}

start_resident() {
  local running
  running="$(pids)"
  if [ -n "$running" ]; then
    echo "ALREADY_RUNNING pids=$running"
    return 0
  fi
  [ -S "$SOCKET" ] || { echo "BLOCKED: NO_SUPPLY_OWNER at $SOCKET"; return 3; }
  if [ ! -f "$RESIDENT_DIR/artifact_grant" ]; then
    echo "BLOCKED: NO_SOAK_GRANT (run run_resident_soak.sh first)"
    return 3
  fi
  prepare_code
  prepare_state
  resolve_model_env
  ( umask 077; printf 'NYA02_MODEL_PROVIDER=%s\nNYA02_MODEL_NAME=%s\nNYA02_MODEL_BASE_URL=%s\nNYA02_MODEL_API_KEY=%s\n' \
      "${NYA02_MODEL_PROVIDER:-}" "${NYA02_MODEL_NAME:-}" "${NYA02_MODEL_BASE_URL:-}" \
      "${NYA02_MODEL_API_KEY:-}" > "$RESIDENT_DIR/model_env" )
  chown "$SVC_USER:$SVC_USER" "$RESIDENT_DIR/model_env"
  chmod 600 "$RESIDENT_DIR/model_env"
  local grant workspace memory
  grant="$(cat "$RESIDENT_DIR/artifact_grant")"
  workspace="$(cat "$RESIDENT_DIR/workspace_id" 2>/dev/null || true)"
  memory="${NYA02_MEMORY_DB:-$RESIDENT_DIR/memory/evidence.db}"
  mkdir -p "$(dirname "$memory")"
  chown -R "$SVC_USER:$SVC_USER" "$(dirname "$memory")"
  chmod 700 "$(dirname "$memory")"

  echo "== commit $(cat "$COMMITFILE") state_root=$STATE_ROOT subject=$SUBJECT poll=${POLL}s"
  echo "== model=${NYA02_MODEL_NAME:-none} key=$([ -n "${NYA02_MODEL_API_KEY:-}" ] && echo present || echo absent)"

  runuser -u "$SVC_USER" -- env \
    NYA02_SUPPLY_SOCKET="$SOCKET" \
    NYA02_SUBJECT="$SUBJECT" \
    NYA02_WORKSPACE_ID="$workspace" \
    NYA02_ARTIFACT_GRANT="$grant" \
    NYA02_RESIDENT_STATE="$STATE_ROOT" \
    CHIYO_LIFE_INSTALL_ROOT="$RESIDENT_DIR" \
    CHIYO_LIFE_SUPPLY_WORKSPACE_ID="$workspace" \
    NYA02_MEMORY_DB="$memory" \
    NYA02_MEMORY_CONTROLS_DB="${NYA02_MEMORY_CONTROLS_DB:-$RESIDENT_DIR/memory/controls.db}" \
    NYA02_MEMORY_SUBJECT="${NYA02_MEMORY_SUBJECT:-$SUBJECT}" \
    "${passthrough_env_args[@]}" \
    nohup python3 "$APP_DIR/components/life/scripts/autonomous_life_resident.py" \
      --state-root "$STATE_ROOT" --poll-seconds "$POLL" "$@" \
    > "$LOG" 2>&1 &
  local launcher=$!
  sleep 3
  local started
  started="$(pids)"
  if [ -z "$started" ]; then
    echo "FAILED_TO_START (see $LOG)"
    tail -20 "$LOG"
    return 4
  fi
  echo "$started" > "$PIDFILE"
  echo "STARTED pids=$started launcher=$launcher log=$LOG"
}

stop_resident() {
  local running
  running="$(pids)"
  if [ -z "$running" ]; then
    echo "NOT_RUNNING"
    return 0
  fi
  echo "stopping pids=$running"
  kill -TERM $running 2>/dev/null || true
  for _ in $(seq 1 40); do
    [ -z "$(pids)" ] && break
    sleep 0.5
  done
  if [ -n "$(pids)" ]; then
    echo "STILL_RUNNING pids=$(pids)"
    return 5
  fi
  echo "STOPPED"
}

status_resident() {
  local running
  running="$(pids)"
  echo "pids=${running:-none}"
  runuser -u "$SVC_USER" -- env NYA02_RESIDENT_STATE="$STATE_ROOT" \
    python3 "$APP_DIR/components/life/scripts/autonomous_life_resident.py" \
    --status --state-root "$STATE_ROOT"
}

case "${1:-status}" in
  start) shift || true; start_resident "$@";;
  stop) stop_resident;;
  status) status_resident;;
  foreground)
    shift || true
    prepare_state
    resolve_model_env
    # The key must never sit on the command line: `ps` shows that to every user on the box.  It
    # goes into a 0600 file the service identity owns, and the resident reads it from there.
    ( umask 077; printf 'NYA02_MODEL_PROVIDER=%s\nNYA02_MODEL_NAME=%s\nNYA02_MODEL_BASE_URL=%s\nNYA02_MODEL_API_KEY=%s\n' \
        "${NYA02_MODEL_PROVIDER:-}" "${NYA02_MODEL_NAME:-}" "${NYA02_MODEL_BASE_URL:-}" \
        "${NYA02_MODEL_API_KEY:-}" > "$RESIDENT_DIR/model_env" )
    chown "$SVC_USER:$SVC_USER" "$RESIDENT_DIR/model_env"
    chmod 600 "$RESIDENT_DIR/model_env"
    grant="$(cat "$RESIDENT_DIR/artifact_grant")"
    workspace="$(cat "$RESIDENT_DIR/workspace_id" 2>/dev/null || true)"
    exec runuser -u "$SVC_USER" -- env \
      NYA02_SUPPLY_SOCKET="$SOCKET" NYA02_SUBJECT="$SUBJECT" \
      NYA02_WORKSPACE_ID="$workspace" NYA02_ARTIFACT_GRANT="$grant" \
      NYA02_RESIDENT_STATE="$STATE_ROOT" CHIYO_LIFE_INSTALL_ROOT="$RESIDENT_DIR" \
      CHIYO_LIFE_SUPPLY_WORKSPACE_ID="$workspace" \
      NYA02_MEMORY_DB="${NYA02_MEMORY_DB:-$RESIDENT_DIR/memory/evidence.db}" \
      NYA02_MEMORY_CONTROLS_DB="${NYA02_MEMORY_CONTROLS_DB:-$RESIDENT_DIR/memory/controls.db}" \
      NYA02_MEMORY_SUBJECT="${NYA02_MEMORY_SUBJECT:-$SUBJECT}" \
      "${passthrough_env_args[@]}" \
      python3 "$REPO/components/life/scripts/autonomous_life_resident.py" \
        --state-root "$STATE_ROOT" --poll-seconds "$POLL" "$@";;
  *) echo "usage: $0 start|stop|status|foreground"; exit 2;;
esac
