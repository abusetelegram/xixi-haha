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
after two consecutive pages whose IDs all exist in the canonical `articles/`
store. An ID found only in `.state/entries.json` or `.cache/` does not count as
published.

This is a fast latest-first heuristic, not an exhaustive reconciliation guarantee.
Use `--full-scan` for the initial catch-up and periodically thereafter to discover
older gaps, backdated articles, or reordered entries. Pagination derives the page
size from the first response instead of hardcoding ten. Duplicate IDs across pages
are merged. The remote API has no snapshot isolation; another full scan may be
needed if entries move while crawling.

`--max-pages N` is a fail-closed request cap. If the updater reaches page `N`
while more pages remain, the run reports `page_limit` and publishes nothing; this
check takes precedence when the second known page is page `N`. A known-page stop
before the cap remains a successful incremental scan, and natural listing
exhaustion on page `N` is also successful. The option does not discard IDs already
queued in `.state/entries.json`. Values must be positive.

### Isolated bounded discovery check

`--data-dir` selects an independent corpus/cache directory. An empty directory
starts with no canonical articles; it does not implicitly import repository data.
The following one-page check is intentionally incomplete when the live listing
advertises additional pages, exits `1`, and publishes no article:

```sh
tmp=$(mktemp -d)
uv run --locked python parse/update.py update --data-dir "$tmp" --max-pages 1
```

Inspect the JSON report for `"stop_reason": "page_limit"`. Only ignored discovery
state/cache files are created under the temporary directory; repository data
remains untouched.

## Data preservation and project compatibility

- Canonical records are deterministic full-record JSON files at
  `$DATA/articles/{id}.json`. The fields are `id`, `title`, `date`, `author`,
  `editor`, `article`, and `text`; consumer aggregate generation is separate from
  this updater.
- Updates are **add-only**. Existing canonical records are never deleted or
  rewritten, even when upstream removes or edits them. Historical records may
  retain legacy quirks; newly fetched articles must contain readable text.
- Checked-in migration inputs such as `result-min.json`, `result-full.tgz`, and
  `v1/xi.json` are historical artifacts. The updater neither reads nor writes
  them and does not support the former `--write-full`/`result.json` workflow.
- Listing state is stored in `.state/entries.json`; diagnostic listing snapshots
  and validated HTML are stored below `.cache/api/` and `.cache/html/`. These are
  noncanonical retry aids and never make an ID count as published.
- State and cache writes use temporary files plus atomic replacement. Every new
  article in a batch is fetched and validated before canonical publication. A
  failure can leave validated cache files for retry but leaves canonical records
  unchanged.
- `.update.lock` prevents concurrent CLI writers to the same data directory and
  spans discovery, downloads, state/cache writes, and publication. Normal exit
  or interruption removes it. After a hard kill, inspect the PID in that file and
  remove the lock **only after confirming no updater is running**.
- Missing state/cache files do not remove canonical records. Existing
  `.state/entries.json` is merged, and `.cache/api/` snapshots are never used to
  skip fresh discovery.

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
10-second timeout, disables retries, and paces requests. In an isolated temporary
data directory it validates the fetched article, cache contract, and canonical
create-only store directly; it never writes repository corpus or cache paths:

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
