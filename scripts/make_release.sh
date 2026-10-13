#!/usr/bin/env bash
# Build the v0.2 release assets from one COMMIT (never from the working tree).
#
#     bash scripts/make_release.sh <commit> [--out DIR]
#
# What it produces, in DIR (default ./dist):
#
#     nyairo-<version>.zip          the source package, extracted at the archive root
#     nyairo-<version>.zip.sha256   `sha256sum` line naming that asset
#
# The asset names are the ones `website/install.sh` and the v0.1.0-rc9 release use:
#
#     https://github.com/L1AN929/nyairo/releases/download/<tag>/nyairo-<tag>.zip[.sha256]
#
# so a tag `v0.2.0-rc1` publishes `nyairo-v0.2.0-rc1.zip`.  The version string is
# read from `MANIFEST.json` **at that commit**, so the package can never be named
# after one version while carrying another version's manifest.
#
# Why a commit and not the working tree: the repository stores text with LF
# (`.gitattributes`), so a Windows checkout on disk is CRLF and its bytes are not
# the bytes of any commit.  `git archive` reads the committed blobs, which are the
# bytes `MANIFEST.json` describes and the bytes `scripts/verify_manifest.py`
# verifies.  A package built from the working tree would fail that verification.
#
# Before it writes anything it extracts the archive into a private staging
# directory and runs the packaged `scripts/verify_manifest.py` there.  A package
# whose own manifest does not verify is never published -- the gate is the same
# script the installer runs on the user's machine.
#
# It never creates a tag, never touches a branch, and never writes into the
# repository except inside the output directory.
set -uo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: bash scripts/make_release.sh <commit> [--out DIR] [--allow-unverified]

  <commit>               any commit-ish (sha, tag, branch). Required.
  --out DIR              where to write the assets (default: ./dist)
  --allow-unverified     skip the packaged verify_manifest.py gate (do not use
                         for a real release; it exists only for diagnostics)
USAGE
}

COMMIT=""
OUT="dist"
ALLOW_UNVERIFIED=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:-}"; [ -n "$OUT" ] || { usage; exit 2; }; shift 2;;
    --allow-unverified) ALLOW_UNVERIFIED=1; shift;;
    -h|--help) usage; exit 0;;
    -*) usage; exit 2;;
    *) [ -z "$COMMIT" ] || { usage; exit 2; }; COMMIT="$1"; shift;;
  esac
done

[ -n "$COMMIT" ] || { usage; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3

for tool in git sha256sum; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 3; }
done

RESOLVED="$(git rev-parse --verify "${COMMIT}^{commit}" 2>/dev/null)" || {
  echo "not a commit: $COMMIT" >&2; exit 3
}

VERSION="$(git show "$RESOLVED:MANIFEST.json" 2>/dev/null | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["version"])')" || {
  echo "cannot read version from MANIFEST.json at $RESOLVED" >&2; exit 3
}
[ -n "$VERSION" ] || { echo "empty version in MANIFEST.json at $RESOLVED" >&2; exit 3; }

TAG="v${VERSION#v}"
ASSET="nyairo-$TAG.zip"
mkdir -p "$OUT" || exit 3
OUT="$(cd "$OUT" && pwd)"
ZIP="$OUT/$ASSET"
SUMS="$ZIP.sha256"

echo "== commit  : $RESOLVED"
echo "== version : $VERSION"
echo "== asset   : $ASSET"
echo "== output  : $OUT"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/nyairo-release.XXXXXXXX")" || exit 3
# Keep intermediates for audit; never delete an existing asset or source tree.

echo
echo "== 1. exporting the committed tree (LF, no .git)"
git archive --format=tar --prefix='' "$RESOLVED" | tar -x -C "$STAGE" || exit 4
echo "   files: $(find "$STAGE" -type f | wc -l)"

echo
echo "== 2. the packaged manifest must verify before anything is published"
if python3 "$STAGE/scripts/verify_manifest.py" --root "$STAGE"; then
  echo "   manifest gate: OK"
elif [ "$ALLOW_UNVERIFIED" = "1" ]; then
  echo "   manifest gate: FAILED (continuing because --allow-unverified was given)" >&2
else
  echo "   manifest gate: FAILED -- refusing to build the asset" >&2
  exit 5
fi

echo
echo "== 3. writing the asset"
[ ! -e "$ZIP" ] && [ ! -e "$SUMS" ] || { echo "asset already exists; choose a fresh --out" >&2; exit 6; }
# Deterministic archive: sorted names, fixed timestamps, no host-specific metadata.
python3 - "$STAGE" "$ZIP" <<'PY' || exit 6
import sys, zipfile
from pathlib import Path

stage, zip_path = Path(sys.argv[1]), Path(sys.argv[2])
names = sorted(p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file())
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in names:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        # The mode comes from the extracted tree, which `tar -x` took from the
        # commit itself -- so the archive records the same modes as the commit.
        info.external_attr = ((stage / name).stat().st_mode & 0xFFFF) << 16
        archive.writestr(info, (stage / name).read_bytes())
print("   entries: %d" % len(names))
PY

SUM="$(sha256sum "$ZIP" | awk '{print $1}')"
printf '%s  %s\n' "$SUM" "$ASSET" > "$SUMS"

echo
echo "== 4. the asset"
( cd "$OUT" && ls -l "$ASSET" "$ASSET.sha256" )
echo "   sha256: $SUM"
( cd "$OUT" && sha256sum -c "$ASSET.sha256" )

cat <<EOF

Next steps (a human decides these; this script does none of them):
  * git tag $TAG $RESOLVED
  * upload $ASSET and $ASSET.sha256 to the release for $TAG
  * only after that, point the website install entry at $TAG
EOF
