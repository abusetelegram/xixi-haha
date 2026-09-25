# Corpus updater

Python 3.9+ CLI for `http://jhsjk.people.cn` (base URL unchanged). Run commands
from the repository root and pass the external canonical data-branch worktree
explicitly; the updater never defaults to repository data.

## Install and run

Install [uv](https://docs.astral.sh/uv/), then sync the locked environment from
the repository root:

```sh
uv sync --locked
DATA=/path/to/article-data-worktree

# Fetch recent missing articles into the canonical create-only store.
uv run --locked python parse/update.py update --data-dir "$DATA"

# Reconcile older gaps as well; this can take a long time.
uv run --locked python parse/update.py update --data-dir "$DATA" --full-scan

# Inspect options, or enable detailed request/exception diagnostics.
uv run --locked python parse/update.py --help
uv run --locked python parse/update.py update --data-dir "$DATA" --log-level DEBUG
```

`update` is the only mode and is also the default positional command. Logs go to
stderr. Exit codes: `0` success, `1` fetch/format/filesystem failure, `2` invalid
arguments, `130` interruption. Log levels: `DEBUG`, `INFO` (default), `WARNING`,
`ERROR`, `CRITICAL`, case-insensitive. Requests are sequential and paced
(`--delay 0.5` seconds minimum between starts), with a 30-second timeout and three
retries for transient failures. Configure with `--delay`, `--timeout`, and
`--retries` (`0` disables retries). Permanent HTTP errors and unknown formats stop
the run instead of silently publishing incomplete data.

### Incremental vs. full scans

Every discovery starts at page 1. Cached page numbers are **not** checkpoints:
new arrivals shift the contents of every subsequent page. Default discovery stops
after two consecutive pages whose IDs are already in **published** `result-min.json`.
Knowing an ID only in `entries.json` does not count as having downloaded it.

This is a fast latest-first heuristic, not an exhaustive reconciliation guarantee.
Use `--full-scan` for the initial catch-up and periodically thereafter to discover
older gaps, backdated articles, or reordered entries. Pagination derives the page
size from the first response instead of hardcoding ten. Duplicate IDs across pages
are merged. The remote API has no snapshot isolation; another full scan may be
needed if entries move while crawling.

`--max-pages N` is a fail-closed guard: if more pages are advertised, the
run reports an incomplete scan and publishes nothing. It does not limit
previously queued IDs in `.state/entries.json`.

### Isolated smoke test

`--data-dir` selects an independent corpus/cache directory. An empty directory
starts a new corpus; it does not implicitly import the repository's data.

```sh
tmp=$(mktemp -d)
cp parse/result-min.json "$tmp/"
uv run --locked python parse/update.py update --data-dir "$tmp" --max-pages 1
```

The repository data remains untouched.

## Data preservation and project compatibility

- The canonical output remains an object keyed by article ID, with exactly the
  existing fields: `title`, `date`, `author`, `editor`, `text` (paragraph array).
  This matches `telegram/index.js`; deploy it as `telegram/xi.json` as before.
- Updates are **add-only**. Existing records are never deleted or rewritten,
  even when upstream removes or edits them. Historical quirks (including 29 empty
  text arrays in the checked-in corpus) are preserved. New articles must have text.
- `result-full.tgz` and `v1/xi.json` are historical artifacts and are never changed.
  `--write-full` additionally merges the full-record list into `result.json`,
  seeding from `result-full.tgz` when needed. Keep the archive and HTML caches:
  upgrading a minimal-only run to full output uses them to recover original HTML.
  If historical HTML is unavailable, full export fails rather than fabricating it.
- `web/` still consumes the **v1 flat string array**, not the v2 ID-keyed object.
  Do not replace `web/xi.json` with `result-min.json`; changing that service's data
  contract is outside this parser update.
- JSON and HTML writes use temporary files plus atomic replacement. All new
  articles must validate before corpus publication. A failure leaves validated
  caches for retry and leaves existing records intact. With `--write-full`, full
  output is published first; each file is atomic, but the two files are not a
  single transaction. Rerunning completes an interrupted publication.
- `.update.lock` prevents concurrent CLI writers to the same data directory.
  Normal exit/interruption removes it. After a hard kill, inspect the PID in that
  file and remove the lock **only after confirming no updater is running**.
- Missing metadata/caches do not remove historical corpus records. Existing
  `entries.json` is merged; old `api/` files are retained only as diagnostic
  snapshots and are never used to skip fresh network discovery.

## Upstream format findings and maintenance

Live inspection on 2026-09-24 found:

- `/testnew/result?page=1&source=2` still returns `status`, `total` (string),
  `curPage`, and `list`. Rows use `article_id`, `title`, `input_date`, `origin_name`.
- `newcontent` is sometimes a truncated preview; it is **not** a full-text source.
- `/article/{id}` still uses `.d2txt_con` and optional `.editor`. Current HTML can
  contain malformed nested `<p><p>` tags. Paragraph extraction handles these,
  inline markup, line breaks, and legacy newline-only content without duplicating
  nested paragraphs or collecting navigation/scripts.
- Article requests worked with User-Agent/Referer headers; a bare urllib request
  received HTTP 403. No stale hardcoded cookies are needed; cookies are session-managed.

There is no evidence that a different endpoint or speculative JSON schema adapter
is needed. `parse_listing`, `normalize_entry`, and `parse_article` isolate upstream
formats from storage. If the site changes, add a fixture and update the relevant
adapter. Unknown structures intentionally fail closed rather than falling back
to whole-page text or truncated previews.

## Tests

The unit suite is network-free. After a locked sync, run it with uv's network
access disabled:

```sh
uv sync --locked
uv run --frozen --offline python -m unittest discover -s parse/tests -v
```

The real-site smoke test is deliberately separate and opt-in locally. It makes
exactly two logical HTTP requests (listing page 1 and its first article), uses a
10-second timeout, disables retries, paces the requests, invokes the canonical
CLI against a temporary data directory, and validates the generated minimal
record. It never writes repository corpus or cache paths:

```sh
uv run --frozen python parse/tests/live_smoke.py
```

CI uses `uv sync --locked`, so a stale lock fails rather than being accepted. Every
matching pull request and push runs the offline unit matrix on Python 3.9 and 3.13
plus the separate, mandatory live-site job; `workflow_dispatch` runs both jobs on
demand. The live job has a two-minute job timeout and does not skip or ignore
upstream failures. Keeping it separate makes upstream availability failures
distinguishable from deterministic parser-test failures.

Use `uv lock --check` to verify that `uv.lock` is consistent with
`pyproject.toml`. Fixtures are small, synthetic structural equivalents of the
observed current/legacy HTML. Coverage includes pagination, stale caches,
add-only/idempotent merging, historical empty data, archive preservation,
interrupted writes, writer locking, retries, canonical CLI behavior, and format failures.
