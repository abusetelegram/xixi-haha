# Corpus updater

Python 3.9+ CLI for `http://jhsjk.people.cn` (base URL unchanged). Run commands
from the repository root; data paths default to `parse/`, not the working directory.

## Install and run

```sh
python3 -m venv parse/.venv
parse/.venv/bin/python -m pip install -r parse/requirements.txt

# Fetch recent missing articles and merge into result-min.json.
parse/.venv/bin/python parse/update.py update

# Reconcile older gaps as well; recommended for the initial migration.
# This scans the entire listing and can take a long time.
parse/.venv/bin/python parse/update.py update --full-scan --write-full

# Inspect options, or enable detailed request/exception diagnostics.
parse/.venv/bin/python parse/update.py --help
parse/.venv/bin/python parse/update.py update --log-level DEBUG
```

`update` is the default mode. Logs go to stderr. Exit codes: `0` success,
`1` fetch/format/filesystem failure, `2` invalid arguments, `130` interruption.
Log levels: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`, case-insensitive.
Requests are sequential and paced (`--delay 0.5` seconds minimum between starts),
with a 30-second timeout and three retries for transient failures. Configure with
`--delay`, `--timeout`, and `--retries` (`0` disables retries). Permanent HTTP errors
and unknown formats stop the run instead of silently publishing incomplete data.

## Modes

| Mode | Network | Behavior |
| --- | --- | --- |
| `update` | Yes | Discover → download → extract/publish |
| `entries` | Yes | Refresh listings and merge `entries.json`; no corpus changes |
| `download` | Yes | Fetch missing/invalid HTML for unpublished IDs in `entries.json` |
| `extract` | No | Validate cached HTML and merge new records into `result-min.json` |

```sh
parse/.venv/bin/python parse/update.py entries --full-scan
parse/.venv/bin/python parse/update.py download
parse/.venv/bin/python parse/update.py extract --write-full
```

The historical `entires.py` (typo), corrected `entries.py`, `articles.py`, and
`articleExt.py` remain wrappers. They accept the same options. `articleExt.py`
also enables `--write-full`, preserving its two-output behavior. They no longer
perform network or filesystem operations merely by being imported.

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

`--max-pages N` explicitly limits discovery (a **partial** scan), useful for smoke
tests. It does not limit previously queued IDs in `entries.json`. A successful
limited run does not mean the whole site has been synchronized.

### Isolated smoke test

`--data-dir` selects an independent corpus/cache directory. An empty directory
starts a new corpus; it does not implicitly import the repository's data.

```sh
tmp=$(mktemp -d)
cp parse/result-min.json "$tmp/"
parse/.venv/bin/python parse/update.py update --data-dir "$tmp" --max-pages 1
```

The repository data remains untouched. To test `--write-full` in that directory,
also copy `parse/result-full.tgz` before running the updater.

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

```sh
parse/.venv/bin/python -m unittest discover -s parse/tests -v
```

Tests are offline. Fixtures are small, synthetic structural equivalents of the
observed current/legacy HTML. Coverage includes pagination, stale caches,
add-only/idempotent merging, historical empty data, archive preservation,
interrupted writes, writer locking, retries, CLI compatibility, and format failures.
