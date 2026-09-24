# Canonical article corpus tools

Python 3.9+ tools for the add-only article corpus. Code stays on `master` (or a
review branch); canonical data stays in a separate checkout of the orphan
`data` branch. Never execute code from the data checkout.

## Install

Run from the code checkout and use the locked dependencies:

```sh
uv sync --locked
```

Every mutating command requires an explicit external `--data-dir`.

## Data contract

Canonical records are deterministic UTF-8 JSON files at
`articles/<positive-decimal-id>.json`. Each has exactly `id`, `title`, `date`,
`author`, `editor`, `article`, and `text`. Routine updates only add files:
existing article edits and deletions fail validation. Historical values,
including 29 empty `text` arrays in the original 12,291 records, are preserved;
new parser-created records must have nonempty text.

Caches and listing state are ignored under `.cache/` and `.state/`. They are
retry aids, not authoritative data. Generated aggregates must be written outside
the data checkout and must not be committed to `data`.

## Initial local import

Use separate code and data worktrees. The import is idempotent and validates the
legacy full/minimal projection before publishing any canonical article:

```sh
CODE=/absolute/path/to/xixi-haha-code
DATA=/absolute/path/to/xixi-haha-data

cd "$CODE"
uv run --locked python parse/corpus.py import-legacy \
  --archive parse/result-full.tgz \
  --minimal parse/result-min.json \
  --data-dir "$DATA"
uv run --locked python parse/corpus.py validate \
  --data-dir "$DATA" \
  --archive parse/result-full.tgz \
  --minimal parse/result-min.json
```

The fixed bootstrap inputs are SHA-256
`41db9865a5b5bbb907aea5fd814bbbabdb9ccf18e0614c7f2b0d28156e12c607`
for `result-full.tgz` and
`79468406fb3594807af95208686c2b23bc8d4aa0fc685d386f55e21416be461c`
for `result-min.json`.

## Incremental and full catch-up

The updater prints one JSON report. Publication requires exit status 0 and
`"complete": true`. Weekly incremental discovery starts at page 1 and stops
after two wholly known pages. A full scan lists every advertised page but still
fetches only IDs absent from the canonical store.

```sh
# Routine incremental run with finite production guards.
uv run --locked python parse/update.py update \
  --data-dir "$DATA" \
  --max-pages 50 --max-additions 200 \
  --delay 1 --timeout 30 --retries 3

# Supervised catch-up/reconciliation with larger finite guards.
uv run --locked python parse/update.py update \
  --data-dir "$DATA" --full-scan \
  --max-pages 2000 --max-additions 5000 \
  --delay 1 --timeout 30 --retries 3

uv run --locked python parse/corpus.py validate --data-dir "$DATA"
```

Reaching either guard is an incomplete failure and must not be committed. The
upstream has no snapshot isolation; repeat supervised full scans until stable.
Network, format, pagination, or article failures publish no partial batch.
Validated caches may remain for retry.

For a local incremental catch-up, capture the starting commit and machine report
before acquisition. First run the publication gate with `--dry-run`; after
reviewing the new article files, repeat the same gate without `--dry-run` to
create one additions-only commit and perform a normal, non-forced push:

```sh
START_DATA_SHA=$(git -C "$DATA" rev-parse HEAD)
SOURCE_SHA=$(git rev-parse HEAD)
REPORT=$(mktemp)
set -o pipefail
uv run --locked python parse/update.py update \
  --data-dir "$DATA" \
  --max-pages 50 --max-additions 200 \
  --delay 1 --timeout 30 --retries 3 | tee "$REPORT"

uv run --locked python scripts/data_pipeline.py publish \
  --data-dir "$DATA" --report "$REPORT" \
  --start-sha "$START_DATA_SHA" --source-sha "$SOURCE_SHA" \
  --run-url "local://supervised-catch-up" --dry-run

# After inspection, omit --dry-run to commit and push only new article files.
uv run --locked python scripts/data_pipeline.py publish \
  --data-dir "$DATA" --report "$REPORT" \
  --start-sha "$START_DATA_SHA" --source-sha "$SOURCE_SHA" \
  --run-url "local://supervised-catch-up"
```

A remote race leaves the local commit for inspection but rejects the push; fetch
the new data tip and rerun acquisition rather than rebasing generated output or
force-pushing.

## Deterministic exports

Export only from a validated exact data commit into a new directory outside the
data checkout:

```sh
CODE_SHA=$(git rev-parse HEAD)
DATA_SHA=$(git -C "$DATA" rev-parse HEAD)
OUT_ROOT=$(mktemp -d)
OUT="$OUT_ROOT/exports"

uv run --locked python parse/export.py \
  --data-dir "$DATA" --output-dir "$OUT" \
  --full-json --archive \
  --code-sha "$CODE_SHA" --data-sha "$DATA_SHA"
```

Outputs are `result-min.json` (Telegram v2 projection), `result.json`,
`result-full.tgz`, and `provenance.json`. The uncompressed full JSON is over
100 MiB and must remain a downloadable CI artifact, never a Git blob. The helper
used by CI additionally verifies clean exact-SHA checkouts:

```sh
HELPER_OUT="$OUT_ROOT/helper-exports"
uv run --locked python scripts/data_pipeline.py export \
  --code-dir "$CODE" --data-dir "$DATA" --output-dir "$HELPER_OUT" \
  --code-sha "$CODE_SHA" --data-sha "$DATA_SHA"
```

## Automation

`.github/workflows/update-data.yml` runs Mondays at 03:17 UTC and supports manual
`incremental`, `full`, and `export-only` dispatches. It uses separate source and
data checkouts, locked uv dependencies, finite scan/addition guards, one normal
(non-forced) data push, exact-SHA exports, and serialized concurrency. The `data`
branch must already exist; otherwise the workflow fails clearly.

Manual dispatch defaults to dry-run. Clear dry-run only after reviewing bounds.
`export-only` requires an exact lowercase 40-hex commit reachable from the
published `data` branch and retries derivative artifacts without changing data.
Successful changed updates upload all aggregate forms plus provenance as a
GitHub Actions artifact and explicitly call the reusable Telegram build with the
same data SHA. No-change runs make no commit and do not rebuild derivatives.

The workflow uses the repository's existing `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` secrets for image publication. No PAT, Pages action, or new
storage service is required. The repository owner may optionally select the
`data` branch/root in GitHub Pages settings; repository article files and the
branch archive remain the primary distribution.

## Compatibility and cutover

The checked-in `parse/result-min.json` and `parse/result-full.tgz` remain
**temporarily** on the code branch to bootstrap the data branch and avoid
breaking old raw URLs. Remove them only in a later explicit cutover after data
publication and consumer verification. `parse/v1/xi.json` and `web/` are a
separate legacy flat-array contract and remain unchanged.

The Telegram container consumes the v2 ID-keyed minimal projection. Its workflow
now generates that projection from an explicit data commit instead of copying a
mutable source-tree aggregate. Tests and pull requests never publish images.

## Tests

```sh
uv sync --locked
uv run --frozen --offline python -m unittest discover -s parse/tests -v
uv lock --check --offline
```

The unit suite is network-free and covers add-only storage, updater guards,
deterministic exports, no-change publication, nonzero acquisition, rejected
mutations/deletions, push conflicts, and export failure cleanup. The optional
bounded live smoke makes exactly one listing and one article request:

```sh
uv run --frozen python parse/tests/live_smoke.py
```
