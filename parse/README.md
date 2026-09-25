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

Caches and listing state are ignored under `.cache/` and `.state/`. Listings are
stored in `.state/entries.json`; diagnostic API snapshots and validated HTML are
stored under `.cache/api/` and `.cache/html/`. They are retry aids, not
authoritative data and never make an ID count as published. The updater lock
spans discovery, cache/state writes, downloads, and publication. Generated
aggregates must be written outside the data checkout and must not be committed
to `data`.

## Routine incremental and optional full reconciliation

The updater prints one JSON report. `update` is its only mode and is also the
default positional command. Publication requires exit status 0 and
`"complete": true`; argument errors exit 2 and interruption exits 130. The
normal weekly incremental run starts at the newest listing edge (page 1) and
stops after two wholly known pages. This depends on the upstream newest-first
listing contract; it is not a completeness proof for older or backdated gaps.
A full scan is an explicit, supervised reconciliation option, never the routine
default, and still fetches only IDs absent from the canonical store. Requests
are sequential; `--delay`, `--timeout`, and `--retries` configure pacing and
bounded transient retries.

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
  --delay 2.1 --timeout 30 --retries 3

uv run --locked python parse/corpus.py validate --data-dir "$DATA"
```

`--max-pages N` is a fail-closed request cap. If advertised pages remain when
page `N` is reached, the report is incomplete with `stop_reason: page_limit` and
publishes nothing; that cap takes precedence when the second known page is page
`N`. A known-page stop before the cap succeeds, as does natural listing
exhaustion exactly at the cap. Guard values must be positive.

Reaching either guard is an incomplete failure and must not be committed. The
upstream has no snapshot isolation; repeat supervised full scans until stable.
Network, format, pagination, or article failures publish no partial batch.
Validated caches may remain for retry. The updater does not read or write the
checked-in migration aggregates and does not implement the historical
`--write-full`/`result.json` updater workflow; aggregate creation is the separate
export operation documented below.

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
force-pushing. For an initial supervised catch-up, keep incremental mode and
raise its finite guards only as needed. Retain the same local data worktree so
ignored `.state/entries.json` and validated `.cache/html/` files can preserve a
discovered backlog and avoid repeat article downloads after interruption. These
resume aids are local-only; do not assume they survive on a fresh Actions runner.

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

Manual dispatch defaults to dry-run and privileged jobs run only when the event ref
is the repository's actual default branch. Incremental dispatches expose
`max_pages` and `max_additions` with defaults 50/200 and validated upper bounds
2000/5000; use larger values only for a supervised initial catch-up. The weekly
schedule always uses 50/200 regardless of manual inputs. Clear dry-run only after
reviewing bounds. Full scans remain an explicit reconciliation mode with fixed
2000/5000 guards and a 2.1-second request delay (the one-second pace produced
403 responses during a long scan); incremental scans retain the one-second
pace. `export-only` requires an exact lowercase 40-hex commit reachable from the
published `data` branch and retries derivative artifacts without changing data.
Successful changed updates upload all aggregate forms plus provenance as a
GitHub Actions artifact and explicitly call the reusable Telegram build with the
same data SHA and exact default-branch source SHA. The reusable consumer accepts
only the default-branch update workflow's schedule/manual contexts, while its
direct path remains limited to default-branch pushes. No-change runs make no
commit and do not rebuild derivatives.

Only the two DockerHub secrets required by the reusable image job are forwarded;
the workflow does not use broad secret inheritance. These source-level event,
ref, caller-path, and SHA checks make the checked-in workflow fail closed when
it is accidentally dispatched against a feature/non-default ref. They are not
a security boundary against a same-repository actor who can modify and execute
a workflow (including removing these checks), nor against a repository
administrator who can change Actions settings. Enforcing that stronger threat
model requires protected environments or repository policy outside this
source-only change.

The workflow uses the repository's existing `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` secrets for image publication. No PAT, Pages action, or new
storage service is required. The repository owner may optionally select the
`data` branch/root in GitHub Pages settings; repository article files and the
branch archive remain the primary distribution.

## Compatibility and completed cutover

Canonical files now live only on the `data` branch. Aggregates are generated
outside that checkout from an exact DATA commit and distributed as Actions
artifacts. The historical raw code-branch paths
`parse/result-min.json`, `parse/result-full.tgz`, and the entire `parse/v1/`
directory were intentionally removed; consumers must use canonical article
files or an exact-commit v2 export.

`parse/corpus.py import-legacy` remains a generic, explicit-input migration tool
and its tests use small offline fixtures. If historical migration investigation
is necessary, retrieve old inputs explicitly from a pre-cutover Git commit into
a temporary directory; do not restore or commit those large duplicate blobs.
The legacy Web and Telegram applications are obsolete and await rebuilding
against the current data source. Their implementation code is unchanged by this
cleanup, and no compatibility with the removed dataset paths is promised.

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
