#!/bin/sh
# Build production Worker assets for Cloudflare's native Git integration.
# This script only validates and packages; publication is an operator-controlled step.
set -eu

SCRIPT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
GIT_ROOT=$(git -C "$SCRIPT_ROOT" rev-parse --show-toplevel)
ROOT=$(CDPATH= cd -- "$GIT_ROOT" && pwd -P)
cd "$ROOT"
SOURCE_SHA=$(git rev-parse HEAD)
SOURCE_TOP=$(CDPATH= cd -- "$(git rev-parse --show-toplevel)" && pwd -P)
test "$SOURCE_TOP" = "$ROOT"
test -z "$(git status --porcelain=v1 --untracked-files=no)"

command -v git >/dev/null
command -v uv >/dev/null
command -v npm >/dev/null
case "$(uv --version)" in
  "uv 0.12.18"*) ;;
  *) echo "build requires uv 0.12.18" >&2; exit 1 ;;
esac
test "$(node --version)" = "v22.20.0"

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
cleanup() { rm -rf -- "$SCRATCH"; }
trap cleanup EXIT HUP INT TERM
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

uv sync --locked --python 3.13
npm ci --ignore-scripts --no-audit --no-fund --prefix worker
uv run --frozen --offline --python 3.13 python parse/corpus.py validate --data-dir "$SCRATCH"
uv run --frozen --offline --python 3.13 python parse/export_worker.py \
  --data-dir "$SCRATCH" --output-dir worker/generated-assets \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
uv run --frozen --offline --python 3.13 python worker/scripts/validate-deployment.py \
  worker/generated-assets "$SOURCE_SHA" "$DATA_SHA"
REEXPORT=$(mktemp -d "${TMPDIR:-/tmp}/xixi-haha-reexport.XXXXXX")
trap 'rm -rf -- "$SCRATCH" "$REEXPORT"' EXIT HUP INT TERM
uv run --frozen --offline --python 3.13 python parse/export_worker.py \
  --data-dir "$SCRATCH" --output-dir "$REEXPORT/assets" \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
diff -qr worker/generated-assets "$REEXPORT/assets"
uv run --frozen --offline --python 3.13 python worker/scripts/generate-test-assets.py
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
