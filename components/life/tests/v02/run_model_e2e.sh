#!/usr/bin/env bash
# Launcher for the real-model end-to-end run.
#
# The provider credentials live in the operator's profile, which the service identity cannot read
# (0600 under /root).  This launcher resolves them as root and hands them to the service identity
# through the process environment -- nothing is copied into the repository or into a readable file.
set -uo pipefail
ROOT="${NYA02_ROOT:-/srv/nya02}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
PROFILE="${NYA02_PROFILE_HOME:-/root/.nyairo}"

[ -f "$ROOT/env.sh" ] && source "$ROOT/env.sh"
NYA02_ARTIFACT_GRANT="$(cat "$ROOT/artifact_grant" 2>/dev/null || true)"
NYA02_WORKSPACE_ID="$(cat "$ROOT/workspace_id" 2>/dev/null || true)"

echo "== resolving the configured provider from $PROFILE (values are not printed)"
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


# config.yaml usually stores a ${VAR} reference, not the key itself.
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

if [ -z "${NYA02_MODEL_API_KEY:-}" ] || [ -z "${NYA02_MODEL_BASE_URL:-}" ] || [ -z "${NYA02_MODEL_NAME:-}" ]; then
  echo "NOT_RUN: the profile at $PROFILE has no usable provider (base_url/model/key)"
  exit 0
fi
echo "== provider=${NYA02_MODEL_PROVIDER} model=${NYA02_MODEL_NAME} key=present"

runuser -u "$SVC_USER" -- env \
  NYA02_SUPPLY_SOCKET="${LIFE_SUPPLY_SOCKET:-}" \
  NYA02_SUBJECT="${NYA02_SUBJECT:-chiyo}" \
  NYA02_WORKSPACE_ID="$NYA02_WORKSPACE_ID" \
  NYA02_ARTIFACT_GRANT="$NYA02_ARTIFACT_GRANT" \
  NYA02_LIFE_STATE="$ROOT/life-state" \
  NYA02_MODEL_PROVIDER="${NYA02_MODEL_PROVIDER}" \
  NYA02_MODEL_NAME="${NYA02_MODEL_NAME}" \
  NYA02_MODEL_BASE_URL="${NYA02_MODEL_BASE_URL}" \
  NYA02_MODEL_API_KEY="${NYA02_MODEL_API_KEY}" \
  python3 "$ROOT/app/components/life/tests/v02/run_model_e2e.py" "$@"
