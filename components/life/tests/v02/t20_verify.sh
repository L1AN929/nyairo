#!/usr/bin/env bash
# T20 verify: run the CURRENT v0.2 code against the OLD v0.1 profile that
# `run_t20_legacy.sh build` produced, and check the eight compatibility items.
#
#   bash t20_verify.sh              # or: run_t20_legacy.sh verify
#
# Root is needed for two things only: the Hermes CLI part of item 1 (the only Hermes venv on
# this box lives under /root, which the profile's own owner cannot read) and starting the
# harness's old Supply owner if it is not running.  Every other check runs as the profile's
# own owner -- the uid the old `setup_profile.py` recorded in chiyo/config.json -- because
# that is the identity the old code's permission rules are written against.
set -uo pipefail

T20="${NYA02_T20:-/srv/nya02/t20}"
OLD="$T20/old"
PROFILE="$T20/profile"
SOCK="$T20/sock/life-supply.sock"
REPO="${NYA02_REPO:-/mnt/d/nyairo-v02}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLI_HOME="$T20/cli-home"
V02="$T20/v02"
LOG="$T20/t20-verify.log"

[ -f "$T20/t20-facts.json" ] || { echo "T20: BLOCKED - run run_t20_legacy.sh build first"; exit 3; }

# The code under test is this commit, with its committed (LF) bytes.  This working tree is CRLF
# (a pre-existing artifact of the checkout), and the v0.2 native code seals its recall
# components by the sha256 of their bytes, so a CRLF copy cannot assemble at all --
# `sealed_component_mismatch:production_consumption_evaluator_v0_3.py` (the sealed constant is

# Item 8 is only meaningful on the state the old code produced.  Once the v0.2 code has opened
# M0, its one-directional migration has already run, so a second verify would be describing a
# moved state instead of the migration: rebuild first.
if python3 -c "
import sqlite3, sys
con = sqlite3.connect('file:$PROFILE/chiyo/state/memory/evidence.sqlite?mode=ro', uri=True)
sys.exit(0 if [v for v in con.execute('select version from schema_migrations') if v[0] == 2] else 1)
"; then
  echo "T20: BLOCKED - this profile was already opened by the v0.2 code (M0 schema v2)."
  echo "            Run 'bash run_t20_legacy.sh build' for a fresh v0.1 profile, then verify again."
  exit 3
fi
# exactly the sha256 of the committed blob).  `git archive` is read-only and extracts exactly
# those committed blobs.
prepare_current_code() {
  local commit
  commit="$(cd "$REPO" && git rev-parse HEAD)"
  echo "== current v0.2 code: $commit (committed bytes extracted to $V02)"
  rm -rf "$V02"; mkdir -p "$V02"
  ( cd "$REPO" && git archive HEAD components chiyo_bundle plugins ) | tar -x -C "$V02"
  find "$V02" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
  echo "$commit" > "$T20/current-code-commit"
}

hermes_python() {
  # The Hermes venv interpreter: an explicit override, else the first one that can import the
  # CLI's own dependencies.  Only the interpreter is borrowed; the code under test is always
  # this commit's tree, pinned by PYTHONPATH below.
  if [ -n "${NYA02_HERMES_PYTHON:-}" ]; then echo "$NYA02_HERMES_PYTHON"; return 0; fi
  local candidate
  for candidate in $(find /root /srv /opt /home -maxdepth 7 \
                       -path '*/vendor/hermes/.venv/bin/python' 2>/dev/null | head -5); do
    if "$candidate" -c 'import dotenv' 2>/dev/null; then echo "$candidate"; return 0; fi
  done
  return 1
}

cli() {  # one real Hermes CLI command against the old profile
  env HOME="$T20/home" HERMES_HOME="$CLI_HOME" PYTHONPATH="$REPO:$REPO/vendor/hermes" \
    "$HERMES_PY" "$REPO/vendor/hermes/hermes" "$@"
}

prepare_current_code

echo "==== [1] the old chat and the Hermes CLI still work"
if [ ! -S "$SOCK" ]; then
  echo "== the old Supply owner is not up; starting it without touching its data root"
  bash "$HERE/run_t20_legacy.sh" owner-start || { echo "T20: BLOCKED - no old owner"; exit 3; }
fi

if HERMES_PY="$(hermes_python)"; then
  # The CLI runs on a byte copy of the profile: it writes its own logs into HERMES_HOME, and
  # the point of item 8 is that the profile the old code produced is left as it was.
  rm -rf "$CLI_HOME"; cp -a "$PROFILE" "$CLI_HOME"
  echo "-- hermes --version"
  cli --version 2>&1 | sed 's/^/   /'
  echo "-- hermes status"
  cli status 2>&1 | sed -n '1,12p' | sed 's/^/   /'
  echo "-- hermes plugins list (the old profile's own plugin)"
  cli plugins list 2>&1 | grep -i "chiyo" | head -3 | sed 's/^/   /'
  echo "-- hermes plugins compat"
  cli plugins compat 2>&1 | sed 's/^/   /'
  echo "-- hermes plugins doctor chiyo (the CLI's real runtime contracts)"
  cli plugins doctor chiyo 2>&1 | sed 's/^/   /'
  echo "   (the CLI ran against $CLI_HOME, a byte copy of $PROFILE)"
else
  echo "T20_CHECK 1b.hermes-cli SKIPPED no Hermes venv interpreter found (set NYA02_HERMES_PYTHON)"
fi

echo
echo "==== checks 1a and 2-8 (as the profile's own owner, on the real profile)"
runuser -u nyaop -- env HERMES_HOME="$PROFILE" NYA02_REPO="$V02" NYA02_HERMES_DEPS="$REPO/vendor/hermes" \
  NYA02_T20="$T20" NYA02_T20_SOCKET="$SOCK" NYA02_T20_OLD="$OLD" \
  python3 "$HERE/t20_verify.py" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}

# Item 7's real subject is the identity that may write at all: the service principal.  It is a
# separate process because the profile owner is a different uid, and the owner authorises by
# SO_PEERCRED -- a JSON field can never claim an identity.
echo
echo "== [7] the service identity's write attempt (no newly granted permission)"
runuser -u nyasvc -- env NYA02_REPO="$V02" NYA02_T20="$T20" NYA02_T20_SOCKET="$SOCK" \
  python3 "$HERE/t20_unauthorized_write.py" 2>&1 | tee -a "$LOG"
RC7=${PIPESTATUS[0]}

PASSED=$(grep -c 'T20_CHECK .* PASS' "$LOG")
FAILEDN=$(grep -c 'T20_CHECK .* FAIL' "$LOG")
echo
echo "==== T20 summary"
grep 'T20_CHECK' "$LOG" | sed 's/^/   /'
if [ "$FAILEDN" -eq 0 ] && [ "$RC" -eq 0 ] && [ "$RC7" -eq 0 ]; then
  echo "T20: PASS ($PASSED checks passed)"
  exit 0
fi
echo "T20: FAIL ($FAILEDN failed, $PASSED passed; verifier rc=$RC, service-write rc=$RC7)"
exit 1
