#!/usr/bin/env bash
# B8-5 driver: prepare the isolated environment's per-group grants, then run experiment A or B.
#
# The isolated Life Supply owner, its two identities and the harness bootstrap are *not* repo
# deliverables -- this assumes the environment is already up (see the batch's scratch tooling).
# Everything the experiments need on top of that is resolved here, as the right identity:
#
#   * one subject + workspace + COMMIT_MANAGED_ARTIFACT grant per group, so two instances can
#     hold genuinely different works without either one being able to touch the other's;
#   * the configured provider, resolved as root and handed to the service identity through the
#     process environment only (nothing is copied into the repository or into a readable file).
#
# Usage:  NYA02_ROOT=/srv/nya02 run_b8_causal.sh a|b [--max-calls N]
set -uo pipefail

ROOT="${NYA02_ROOT:-/srv/nya02}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
MODE="${1:-a}"
shift || true
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
OP_USER="${NYA02_OP_USER:-nyaop}"
PROFILE="${NYA02_PROFILE_HOME:-/root/.nyairo}"
MAX_CALLS="${NYA02_MAX_MODEL_CALLS:-6}"
# A fresh grant/workspace per invocation: the owner dedupes on the operation id, so reusing one
# across runs would replay the previous run's grant instead of issuing this run's.
RUN_TAG="${NYA02_CA_RUN:-$(date +%s)-$$}"

[ -f "$ROOT/env.sh" ] && source "$ROOT/env.sh"
SOCKET="${NYA02_SUPPLY_SOCKET:-${LIFE_SUPPLY_SOCKET:-$ROOT/sock/life-supply.sock}}"
[ -S "$SOCKET" ] || { echo "NOT_RUN: no Life Supply owner at $SOCKET"; exit 0; }

case "$MODE" in
  a) HARNESS="b8_causal_exp_a.py"; TAGS="T1A T1B T2A T2B T3A T3B"; PREFIX="ca"; SUBJ_PREFIX="chiyo-ca-";;
  b) HARNESS="b8_causal_exp_b.py"; TAGS="S1 S2 S3"; PREFIX="cb"; SUBJ_PREFIX="chiyo-cb-";;
  *) echo "usage: $0 a|b"; exit 2;;
esac

# ---- the code the service identity executes ------------------------------------------
rm -rf "$ROOT/app"; mkdir -p "$ROOT/app"
cp -r "$REPO/components" "$ROOT/app/components"
chmod -R a+rX "$ROOT/app"
find "$ROOT/app" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# ---- a grant tool with unique operation ids, so several subjects can each hold one ----
cat > "$ROOT/tools/ca_mint.py" <<'PY'
import json, os, sys
sys.path.insert(0, "/srv/nya02/tools")
from rpc import call

SUBJECT = os.environ["NYA02_SUBJECT"]
TAG = os.environ["NYA02_CA_TAG"]
RUN = os.environ["NYA02_CA_RUN"]
WHICH = sys.argv[1]

if WHICH == "workspace":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": f"op:ca:grant:workspace:{TAG}:{RUN}",
        "capability": "PROVISION_PERSONAL_WORKSPACE", "scope": "workspace:personal",
        "target": f"workspace:{SUBJECT}", "use_mode": "REUSABLE"}})
elif WHICH == "artifact":
    res = call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "operation_id": f"op:ca:grant:artifact:{TAG}:{RUN}",
        "capability": "COMMIT_MANAGED_ARTIFACT", "scope": "artifact:personal",
        "target": sys.argv[2], "use_mode": "REUSABLE"}})
else:
    res = call({"op": "provision_workspace", "grant_id": sys.argv[2],
                "operation_key": f"op:ca:workspace:provision:{TAG}:{RUN}",
                "workspace": {"subject_id": SUBJECT, "visibility": "PRIVATE",
                              "artifact_scope": "PERSONAL"}})
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.exit(0 if res.get("status") in {"ALLOW", "OK", "NO_CHANGE"} else 3)
PY
chmod 755 "$ROOT/tools/ca_mint.py"

mint() {
  local S="$1" TAG="$2" WHICH="$3" EXTRA="${4:-}"
  if [ "$WHICH" = "provision" ]; then
    runuser -u "$SVC_USER" -- env NYA02_SUBJECT="$S" NYA02_CA_TAG="$TAG" NYA02_CA_RUN="$RUN_TAG" \
      python3 "$ROOT/tools/ca_mint.py" provision "$EXTRA"
  else
    runuser -u "$OP_USER" -- env NYA02_SUBJECT="$S" NYA02_CA_TAG="$TAG" NYA02_CA_RUN="$RUN_TAG" \
      python3 "$ROOT/tools/ca_mint.py" "$WHICH" "$EXTRA"
  fi
}

CA_ROOT="$ROOT/b8-causal-$PREFIX"
rm -rf "$CA_ROOT"; mkdir -p "$CA_ROOT"
chown -R "$SVC_USER:$SVC_USER" "$CA_ROOT"; chmod 700 "$CA_ROOT"

GROUPS_FILE="$ROOT/${PREFIX}_groups.json"
: > "$GROUPS_FILE"
for TAG in $TAGS; do
  # A subject per run, so provisioning always creates a fresh workspace instead of answering
  # NO_CHANGE for one this run would then have to look up.
  S="${SUBJ_PREFIX}${TAG}-${RUN_TAG}"
  WSG=$(mint "$S" "$TAG" workspace | python3 -c "import json,sys;print(json.load(sys.stdin)['grant_id'])")
  WS=$(mint "$S" "$TAG" provision "$WSG" | python3 -c "import json,sys;print((json.load(sys.stdin).get('workspace') or {})['workspace_id'])")
  AG=$(mint "$S" "$TAG" artifact "workspace:$WS" | python3 -c "import json,sys;print(json.load(sys.stdin)['grant_id'])")
  echo "$TAG subject=$S workspace=$WS grant=$AG"
  python3 - "$GROUPS_FILE" "$TAG" "$S" "$WS" "$AG" <<'PY'
import json, sys
path, tag, subject, ws, grant = sys.argv[1:6]
try:
    data = json.load(open(path))
except Exception:
    data = {}
data[tag] = {"subject": subject, "workspace_id": ws, "grant_id": grant}
json.dump(data, open(path, "w"))
PY
done
CA_GROUPS=$(cat "$GROUPS_FILE")

# ---- the provider, resolved as root and passed through the environment only -----------
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

MODEL_MODE="NOT_RUN"
if [ -n "${NYA02_MODEL_API_KEY:-}" ] && [ -n "${NYA02_MODEL_BASE_URL:-}" ] && [ -n "${NYA02_MODEL_NAME:-}" ]; then
  MODEL_MODE="configured (${NYA02_MODEL_NAME})"
fi
echo "== mode=$MODE model=$MODEL_MODE call-cap=$MAX_CALLS"

GROUP_VAR="NYA02_CAUSAL_GROUPS"
[ "$MODE" = "b" ] && GROUP_VAR="NYA02_CAUSAL_B_GROUPS"

# The provider key goes into a 0600 file the service identity owns, never onto the command line:
# `ps` shows a command line to every user on the box, an inherited environment it does not.  The
# harness loads the file itself (``autonomous_life_assembly.load_model_env_file``).
MODEL_ENV_FILE="${NYA02_MODEL_ENV_FILE:-$ROOT/model_env}"
( umask 077; printf 'NYA02_MODEL_PROVIDER=%s\nNYA02_MODEL_NAME=%s\nNYA02_MODEL_BASE_URL=%s\nNYA02_MODEL_API_KEY=%s\n' \
    "${NYA02_MODEL_PROVIDER:-}" "${NYA02_MODEL_NAME:-}" "${NYA02_MODEL_BASE_URL:-}" \
    "${NYA02_MODEL_API_KEY:-}" > "$MODEL_ENV_FILE" )
chown "$SVC_USER:$SVC_USER" "$MODEL_ENV_FILE"
chmod 600 "$MODEL_ENV_FILE"

runuser -u "$SVC_USER" -- env \
  NYA02_SUPPLY_SOCKET="$SOCKET" \
  NYA02_CAUSAL_ROOT="$CA_ROOT" \
  "$GROUP_VAR=$CA_GROUPS" \
  NYA02_MAX_MODEL_CALLS="$MAX_CALLS" \
  NYA02_MODEL_PROVIDER="${NYA02_MODEL_PROVIDER:-}" \
  NYA02_MODEL_ENV_FILE="$MODEL_ENV_FILE" \
  python3 "$ROOT/app/components/life/tests/v02/$HARNESS" "$@"
RC=$?
echo "rc=$RC"
exit $RC
