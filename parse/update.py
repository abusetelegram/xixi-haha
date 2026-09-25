#!/usr/bin/env python3
"""Fetch new jhsjk articles into an external canonical data directory."""

import argparse
import json
import logging
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from bs4 import BeautifulSoup

from corpus import (CorpusError, create_articles, load_articles, normalize_id,
                    writer_lock)

BASE_URL = "http://jhsjk.people.cn"
LOGGER = logging.getLogger(__name__)


class FormatError(ValueError):
    """The upstream response or local fetch state is not safe to use."""


def article_id(value) -> str:
    try:
        return normalize_id(value)
    except CorpusError as exc:
        raise FormatError(str(exc)) from exc


def normalize_entry(row: dict) -> dict:
    """Validate and retain only listing fields used to construct a record."""
    if not isinstance(row, dict):
        raise FormatError("Listing row must be an object")
    result = {"article_id": article_id(row.get("article_id"))}
    for key in ("title", "input_date"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FormatError("Listing row is missing {}".format(key))
        result[key] = value
    if "origin_name" not in row:
        raise FormatError("Listing row is missing origin_name")
    author = row["origin_name"]
    if author is None:
        # The live v2 listing uses explicit null for a small number of
        # unattributed articles; the historical corpus represents these as "".
        author = ""
    elif not isinstance(author, str):
        raise FormatError("Listing row has invalid origin_name")
    result["origin_name"] = author
    return result


def parse_listing(payload: dict, page: int) -> tuple:
    """Explicit adapter for the current API; unknown formats fail closed."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise FormatError("Expected a successful listing response")
    try:
        total = int(str(payload["total"]))
        current = int(str(payload["curPage"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise FormatError("Invalid listing pagination") from exc
    if total < 0 or current != page or not isinstance(payload.get("list"), list):
        raise FormatError("Invalid listing page {}".format(page))
    rows = [normalize_entry(row) for row in payload["list"]]
    ids = [row["article_id"] for row in rows]
    if total and not rows:
        raise FormatError("Unexpected empty listing page {}".format(page))
    if len(rows) > total or len(set(ids)) != len(ids):
        raise FormatError("Inconsistent listing count or duplicate IDs on page {}".format(page))
    return rows, total


def parse_article(html: str, entry: dict) -> dict:
    """Parse one full canonical record while preserving paragraph boundaries."""
    entry = normalize_entry(entry)
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".d2txt_con")
    if body is None:
        raise FormatError("Article {} has no .d2txt_con body".format(entry["article_id"]))
    raw_body = str(body)
    editor = soup.select_one(".editor")
    editor_text = editor.get_text("", strip=True) if editor else "不明"
    for unwanted in body.select("script, style, noscript, .editor"):
        unwanted.decompose()
    for br in body.find_all("br"):
        br.replace_with("\n")
    for block in body.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "tr"]):
        block.insert_before("\n")
        block.insert_after("\n")
    text = [line.strip() for line in body.get_text().splitlines() if line.strip()]
    if not text:
        raise FormatError("Article {} has no readable text".format(entry["article_id"]))
    return {
        "id": entry["article_id"],
        "title": BeautifulSoup(entry["title"], "html.parser").get_text().strip(),
        "date": entry["input_date"],
        "author": entry["origin_name"],
        "editor": editor_text or "不明",
        "article": raw_body,
        "text": text,
    }


def atomic_write(path: Path, content: str) -> None:
    """Atomically replace ignored cache/state files, never canonical articles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix="." + path.name, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FormatError("Invalid JSON state {}: {}".format(path, exc)) from exc


def state_path(directory: Path) -> Path:
    return Path(directory) / ".state" / "entries.json"


def api_cache_path(directory: Path, page: int) -> Path:
    return Path(directory) / ".cache" / "api" / (str(page) + ".json")


def html_cache_path(directory: Path, aid: str) -> Path:
    return Path(directory) / ".cache" / "html" / (article_id(aid) + ".html")


def load_entries(directory: Path) -> dict:
    rows = read_json(state_path(directory), [])
    if not isinstance(rows, list):
        raise FormatError("entries state must be a list")
    result = {}
    for source in rows:
        row = normalize_entry(source)
        aid = row["article_id"]
        if aid in result:
            raise FormatError("Duplicate article ID {} in entries state".format(aid))
        result[aid] = row
    return result


class Client:
    def __init__(self, delay: float = 0.5, timeout: int = 30, retries: int = 3):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.last_request = None

    def get(self, path: str) -> str:
        url = BASE_URL + path
        for attempt in range(self.retries + 1):
            if self.last_request is not None:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            request = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; xixi-haha-corpus-updater/4.0)",
                "Referer": BASE_URL + "/",
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            })
            LOGGER.debug("GET %s (attempt %d/%d)", url, attempt + 1, self.retries + 1)
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset)
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2 ** attempt
            except (URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise
                wait = 2 ** attempt
            LOGGER.warning("Retrying %s in %.1fs", url, wait)
            time.sleep(wait)
        raise AssertionError("unreachable")

    def listing(self, page: int) -> dict:
        path = "/testnew/result?" + urlencode({"page": page, "source": 2})
        try:
            return json.loads(self.get(path))
        except json.JSONDecodeError as exc:
            raise FormatError("Listing is not JSON (possibly an error/challenge page)") from exc

    def article(self, aid: str) -> str:
        return self.get("/article/" + article_id(aid))


def discover(directory: Path, client, known, full_scan: bool = False,
             max_pages=None) -> dict:
    """List newest-first and return entries plus a machine-readable scan report.

    Cross-page overlap is expected when pagination moves and is deduplicated.  A
    repeated ID with conflicting metadata, a changed advertised total, or a
    repeated whole page fails closed.  ``max_pages`` returns an explicit
    incomplete report rather than claiming synchronization.
    """
    directory = Path(directory)
    if (max_pages is not None
            and (isinstance(max_pages, bool) or not isinstance(max_pages, int)
                 or max_pages <= 0)):
        raise FormatError("max_pages must be a positive integer")
    known_ids = set(known)
    retained = load_entries(directory)
    fresh = {}
    previous_ids = None
    known_pages = 0
    page = 1
    page_count = None
    advertised_total = None
    page_size = None
    overlaps = 0
    pages_visited = 0
    complete = True
    stop_reason = "listing_exhausted"

    while page_count is None or page <= page_count:
        rows, total = parse_listing(client.listing(page), page)
        pages_visited += 1
        if advertised_total is None:
            advertised_total = total
            page_size = len(rows)
            page_count = math.ceil(total / page_size) if rows else 1
        elif total != advertised_total:
            raise FormatError(
                "Listing total changed from {} to {} on page {}".format(
                    advertised_total, total, page))
        if page < page_count and len(rows) != page_size:
            raise FormatError(
                "Listing page size changed from {} to {} on non-final page {}".format(
                    page_size, len(rows), page))
        if page == page_count and len(rows) > page_size:
            raise FormatError(
                "Final listing page {} exceeds initial page size {}".format(
                    page, page_size))
        ids = {row["article_id"] for row in rows}
        if ids and ids == previous_ids:
            raise FormatError("Listing repeated page {} instead of advancing".format(page))
        previous_ids = ids
        for row in rows:
            aid = row["article_id"]
            previous = fresh.get(aid)
            if previous is not None:
                overlaps += 1
                if previous != row:
                    raise FormatError("Conflicting listing metadata for article {}".format(aid))
            else:
                fresh[aid] = row
        write_json(api_cache_path(directory, page), rows)
        known_pages = known_pages + 1 if ids and ids <= known_ids else 0
        # Exhausting the advertised listing is complete even when it lands
        # exactly on the configured cap.  Otherwise the cap takes precedence
        # over the incremental heuristic at that same page.
        if page >= page_count:
            break
        if max_pages is not None and page >= max_pages:
            complete = False
            stop_reason = "page_limit"
            break
        if not full_scan and known_pages >= 2:
            stop_reason = "two_known_pages"
            break
        page += 1

    merged = dict(retained)
    merged.update(fresh)
    write_json(state_path(directory), [merged[aid] for aid in sorted(merged, key=int)])
    missing = sorted(set(merged) - known_ids, key=int)
    return {
        "entries": merged,
        "pages": pages_visited,
        "advertised_total": advertised_total or 0,
        "listed": len(fresh),
        "overlaps": overlaps,
        "missing_ids": missing,
        "stop_reason": stop_reason,
        "complete": complete,
    }


def cached_article(directory: Path, entry: dict):
    path = html_cache_path(directory, entry["article_id"])
    if not path.exists():
        return None
    try:
        html = path.read_text(encoding="utf-8")
        return html, parse_article(html, entry)
    except (OSError, UnicodeError, FormatError):
        LOGGER.warning("Invalid cached article %s; download required", entry["article_id"])
        return None


def fetch_missing(directory: Path, client, entries: dict, known) -> dict:
    """Fetch and parse all missing records without mutating the canonical store."""
    records = []
    downloaded = 0
    cache_hits = 0
    for aid in sorted(set(entries) - set(known), key=int, reverse=True):
        entry = entries[aid]
        cached = cached_article(directory, entry)
        if cached is None:
            html = client.article(aid)
            record = parse_article(html, entry)
            atomic_write(html_cache_path(directory, aid), html)
            downloaded += 1
        else:
            _, record = cached
            cache_hits += 1
        records.append(record)
    return {"records": records, "downloaded": downloaded, "cache_hits": cache_hits}


def run_update(directory: Path, client, full_scan: bool = False,
               max_pages=None, max_additions=None) -> tuple:
    """Acquire and publish one batch while holding the canonical writer lock."""
    directory = Path(directory)
    with writer_lock(directory) as writer:
        known = load_articles(directory)
        scan = discover(directory, client, set(known), full_scan, max_pages)
        summary = {
            "status": "success" if scan["complete"] else "incomplete",
            "mode": "full" if full_scan else "incremental",
            "complete": scan["complete"],
            "stop_reason": scan["stop_reason"],
            "pages": scan["pages"],
            "advertised_total": scan["advertised_total"],
            "listed": scan["listed"],
            "overlaps": scan["overlaps"],
            "known": len(known),
            "missing": len(scan["missing_ids"]),
            "downloaded": 0,
            "cache_hits": 0,
            "added": 0,
            "records": len(known),
        }
        if not scan["complete"]:
            return summary, 1
        if max_additions is not None and len(scan["missing_ids"]) > max_additions:
            summary.update(status="incomplete", complete=False, stop_reason="addition_limit")
            return summary, 1

        fetched = fetch_missing(directory, client, scan["entries"], known)
        # The writer lease spans discovery/state/cache writes and publication;
        # passing it avoids reacquiring the same non-reentrant lock.
        added = create_articles(
            directory, fetched["records"], strict_content=True, writer=writer)
        final = load_articles(directory)
        summary.update(
            downloaded=fetched["downloaded"], cache_hits=fetched["cache_hits"],
            added=added, records=len(final))
        return summary, 0


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def nonnegative_float(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("update", nargs="?", choices=("update",), default="update")
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="external canonical data-branch worktree")
    parser.add_argument("--full-scan", action="store_true",
                        help="list every advertised page, but fetch only missing IDs")
    parser.add_argument("--max-pages", type=positive_int,
                        help="guard: fail as incomplete if more pages are advertised")
    parser.add_argument("--max-additions", type=nonnegative_int,
                        help="guard: fail before downloads/publication above this count")
    parser.add_argument("--delay", type=nonnegative_float, default=0.5,
                        help="minimum seconds between requests (default: 0.5)")
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--retries", type=nonnegative_int, default=3,
                        help="retries after transient errors (default: 3)")
    parser.add_argument("--log-level", type=str.upper,
                        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
                        default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    try:
        summary, code = run_update(
            args.data_dir.resolve(), Client(args.delay, args.timeout, args.retries),
            args.full_scan, args.max_pages, args.max_additions)
    except KeyboardInterrupt:
        summary = {"status": "failed", "complete": False,
                   "stop_reason": "interrupted", "error": "interrupted"}
        code = 130
    except (CorpusError, FormatError, OSError, HTTPError, URLError,
            TimeoutError, ConnectionError) as exc:
        LOGGER.error("Update failed: %s", exc, exc_info=LOGGER.isEnabledFor(logging.DEBUG))
        summary = {"status": "failed", "complete": False,
                   "stop_reason": "error", "error": str(exc)}
        code = 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
