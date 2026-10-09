#!/bin/sh
# Build production Worker assets for Cloudflare's native Git integration.
# This script only validates and packages; publication is an operator-controlled step.
set -eu

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "build requires $1" >&2
    exit 1
  }
}

require_command git
require_command python3.13
require_command node
require_command npm
case "$(python3.13 --version 2>&1)" in
  "Python 3.13."*) ;;
  *) echo "build requires Python 3.13" >&2; exit 1 ;;
esac
test "$(node --version)" = "v22.20.0" || {
  echo "build requires Node v22.20.0" >&2
  exit 1
}

UV_BOOTSTRAP=
SCRATCH=
REEXPORT=
cleanup() {
  test -z "$REEXPORT" || rm -rf -- "$REEXPORT"
  test -z "$SCRATCH" || rm -rf -- "$SCRATCH"
  test -z "$UV_BOOTSTRAP" || rm -rf -- "$UV_BOOTSTRAP"
}
trap cleanup EXIT HUP INT TERM

UV=
UV_CANDIDATE=$(command -v uv 2>/dev/null || :)
if test -n "$UV_CANDIDATE" && test -x "$UV_CANDIDATE"; then
  UV=$(CDPATH= cd -- "$(dirname -- "$UV_CANDIDATE")" && pwd -P)/$(basename -- "$UV_CANDIDATE")
  case "$("$UV" --version 2>/dev/null || :)" in
    "uv 0.12.18"|"uv 0.12.18 "*) ;;
    *) UV= ;;
  esac
fi
if test -z "$UV"; then
  UV_BOOTSTRAP=$(mktemp -d "${TMPDIR:-/tmp}/xixi-haha-uv.XXXXXX")
  if ! python3.13 -m venv "$UV_BOOTSTRAP/venv"; then
    echo "failed to create isolated Python 3.13 environment for uv" >&2
    exit 1
  fi
  if ! "$UV_BOOTSTRAP/venv/bin/python" -m pip --isolated install \
      --disable-pip-version-check --no-input --only-binary=:all: \
      --index-url https://pypi.org/simple --timeout 30 --retries 2 \
      "uv==0.12.18"; then
    echo "failed to install uv 0.12.18 from public PyPI" >&2
    exit 1
  fi
  UV="$UV_BOOTSTRAP/venv/bin/uv"
  test -x "$UV" || {
    echo "uv 0.12.18 installation did not create an executable" >&2
    exit 1
  }
  case "$("$UV" --version 2>/dev/null || :)" in
    "uv 0.12.18"|"uv 0.12.18 "*) ;;
    *)
      echo "installed uv did not report version 0.12.18" >&2
      exit 1
      ;;
  esac
fi

SCRIPT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
GIT_ROOT=$(git -C "$SCRIPT_ROOT" rev-parse --show-toplevel)
ROOT=$(CDPATH= cd -- "$GIT_ROOT" && pwd -P)
cd "$ROOT"
SOURCE_SHA=$(git rev-parse HEAD)
SOURCE_TOP=$(CDPATH= cd -- "$(git rev-parse --show-toplevel)" && pwd -P)
test "$SOURCE_TOP" = "$ROOT"
test -z "$(git status --porcelain=v1 --untracked-files=no)"

DATA_REPOSITORY_URL=${DATA_REPOSITORY_URL:-https://github.com/abusetelegram/xixi-haha.git}
PUBLISHED_DATA_SHA=$(git ls-remote --exit-code "$DATA_REPOSITORY_URL" refs/heads/data | awk 'NR == 1 {print $1}')
printf '%s\n' "$PUBLISHED_DATA_SHA" | grep -Eq '^[0-9a-f]{40}$' || {
  echo "data branch did not resolve to an exact lowercase commit SHA" >&2
  exit 1
}
DATA_SHA=${DATA_SHA:-$PUBLISHED_DATA_SHA}
printf '%s\n' "$DATA_SHA" | grep -Eq '^[0-9a-f]{40}$' || {
  echo "DATA_SHA must be an exact lowercase 40-hex commit SHA" >&2
  exit 1
}

SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/xixi-haha-data.XXXXXX")
git -C "$SCRATCH" init -q
git -C "$SCRATCH" remote add origin "$DATA_REPOSITORY_URL"
git -C "$SCRATCH" fetch --no-tags origin refs/heads/data:refs/remotes/origin/data
test "$(git -C "$SCRATCH" rev-parse refs/remotes/origin/data)" = "$PUBLISHED_DATA_SHA"
if ! git -C "$SCRATCH" cat-file -e "$DATA_SHA^{commit}" 2>/dev/null; then
  git -C "$SCRATCH" fetch --no-tags origin "$DATA_SHA"
fi
git -C "$SCRATCH" merge-base --is-ancestor "$DATA_SHA" refs/remotes/origin/data
git -C "$SCRATCH" checkout -q --detach "$DATA_SHA"
test "$(git -C "$SCRATCH" rev-parse HEAD)" = "$DATA_SHA"
test -z "$(git -C "$SCRATCH" status --porcelain=v1 --untracked-files=all)"

"$UV" sync --locked --python 3.13
npm ci --ignore-scripts --no-audit --no-fund --prefix worker
"$UV" run --frozen --offline --python 3.13 python parse/corpus.py validate --data-dir "$SCRATCH"
"$UV" run --frozen --offline --python 3.13 python parse/export_worker.py \
  --data-dir "$SCRATCH" --output-dir worker/generated-assets \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
"$UV" run --frozen --offline --python 3.13 python worker/scripts/validate-deployment.py \
  worker/generated-assets "$SOURCE_SHA" "$DATA_SHA"
REEXPORT=$(mktemp -d "${TMPDIR:-/tmp}/xixi-haha-reexport.XXXXXX")
"$UV" run --frozen --offline --python 3.13 python parse/export_worker.py \
  --data-dir "$SCRATCH" --output-dir "$REEXPORT/assets" \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
diff -qr worker/generated-assets "$REEXPORT/assets"
"$UV" run --frozen --offline --python 3.13 python worker/scripts/generate-test-assets.py
(
  cd worker
  npm run typecheck
  npm test
  WRANGLER_SEND_METRICS=false npm run test:runtime:deployment
  WRANGLER_SEND_METRICS=false npm run package:deployment
)
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
test -z "$(git status --porcelain=v1 --untracked-files=no)"
printf 'built source=%s data=%s\n' "$SOURCE_SHA" "$DATA_SHA"
