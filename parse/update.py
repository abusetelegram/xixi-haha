#!/usr/bin/env python3
"""Add-only updater for the jhsjk corpus; see README.md for safety semantics."""

import argparse
from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from bs4 import BeautifulSoup

BASE_URL = "http://jhsjk.people.cn"
DEFAULT_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)


class FormatError(ValueError):
    """The upstream response or local data is not safe to publish."""


def article_id(value) -> str:
    # Reject unsafe filenames as well as silently changed identifier formats.
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise FormatError("Expected a numeric article_id, got {!r}".format(value))
    return str(value)


def normalize_entry(row: dict) -> dict:
    if not isinstance(row, dict):
        raise FormatError("Listing row must be an object")
    result = dict(row)
    result["article_id"] = article_id(row.get("article_id"))
    for key in ("title", "input_date", "origin_name"):
        if not isinstance(row.get(key), str) or (key != "origin_name" and not row[key].strip()):
            raise FormatError("Listing row is missing {}".format(key))
    return result


def parse_listing(payload: dict, page: int) -> tuple[list[dict], int]:
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
    if total and not rows:
        raise FormatError("Unexpected empty listing page {}".format(page))
    if len(rows) > total or len({row["article_id"] for row in rows}) != len(rows):
        raise FormatError("Inconsistent listing count or duplicate IDs")
    return rows, total


def parse_article(html: str, entry: dict) -> dict:
    """Keep paragraph boundaries, but never split sentences at inline tags."""
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
    # Live pages have nested <p><p> markup. Text-node traversal via get_text
    # avoids duplicate outer/inner paragraphs, unlike looping over every <p>.
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


def read_json(path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False) + "\n")


@contextmanager
def corpus_lock(directory: Path):
    """One writer per data directory; a hard-killed process leaves a visible lock."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".update.lock"
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise FormatError("{} exists; another updater may be running. Remove it only after checking its PID".format(path)) from exc
    try:
        with handle:
            handle.write(str(os.getpid()) + "\n")
        yield
    finally:
        path.unlink()


def load_corpus(directory: Path) -> dict:
    data = read_json(directory / "result-min.json", {})
    if not isinstance(data, dict):
        raise FormatError("result-min.json must be an ID-keyed object")
    for aid, record in data.items():
        article_id(aid)
        if not isinstance(record, dict):
            raise FormatError("Invalid saved article {}".format(aid))
        for field in ("title", "date", "author", "editor"):
            if not isinstance(record.get(field), str):
                raise FormatError("Invalid saved {} for {}".format(field, aid))
        # Historical records include empty text arrays: preserve them verbatim,
        # while parse_article requires nonempty text for all new records.
        if (not isinstance(record.get("text"), list)
                or not all(isinstance(line, str) for line in record["text"])):
            raise FormatError("Invalid saved text for {}".format(aid))
    return data


def load_entries(directory: Path) -> dict:
    rows = read_json(directory / "entries.json", [])
    if not isinstance(rows, list):
        raise FormatError("entries.json must be a list")
    return {row["article_id"]: row for row in map(normalize_entry, rows)}


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
                "User-Agent": "Mozilla/5.0 (compatible; xixi-haha-corpus-updater/3.0)",
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

    def listing(self, page: int) -> dict:
        path = "/testnew/result?" + urlencode({"page": page, "source": 2})
        try:
            return json.loads(self.get(path))
        except json.JSONDecodeError as exc:
            raise FormatError("Listing is not JSON (possibly an error/challenge page)") from exc

    def article(self, aid: str) -> str:
        return self.get("/article/" + article_id(aid))


def discover(directory, client, known, full_scan=False, max_pages=None):
    entries = load_entries(directory)
    fresh = {}
    previous_ids = None
    known_pages = 0
    page = 1
    page_count = None
    stop_reason = "end of listing"
    while page_count is None or page <= page_count:
        rows, total = parse_listing(client.listing(page), page)
        if page_count is None:
            # Do not assume ten records/page, or request an extra exact-multiple page.
            page_count = math.ceil(total / len(rows)) if rows else 1
            LOGGER.info("Listing: %d records, about %d pages", total, page_count)
        ids = {row["article_id"] for row in rows}
        if ids and ids == previous_ids:
            raise FormatError("Listing repeated page {} instead of advancing".format(page))
        previous_ids = ids
        for row in rows:
            fresh.setdefault(row["article_id"], row)
        # Snapshots are diagnostic only: a cached page number is never a cursor.
        write_json(directory / "api" / str(page), rows)
        known_pages = known_pages + 1 if ids and ids <= known else 0
        if not full_scan and known_pages >= 2:
            stop_reason = "two consecutive pages already in result-min.json"
            break
        if max_pages is not None and page >= max_pages and page < page_count:
            stop_reason = "--max-pages limit (partial scan)"
            break
        page += 1
    entries.update(fresh)
    write_json(directory / "entries.json", list(entries.values()))
    LOGGER.info("Discovered %d distinct IDs; stopped at %s", len(fresh), stop_reason)
    return entries


def cached_article(directory, entry):
    path = directory / "articles" / entry["article_id"]
    if path.exists():
        try:
            html = path.read_text(encoding="utf-8")
            parse_article(html, entry)
            return html
        except (FormatError, UnicodeError):
            # Old versions cached even HTTP errors; do not trust file existence.
            LOGGER.warning("Invalid cached article %s; download required", entry["article_id"])
            return None
    return None


def download(directory, client, entries, known):
    pending = [entry for aid, entry in entries.items() if aid not in known]
    for index, entry in enumerate(pending, 1):
        if cached_article(directory, entry) is not None:
            continue
        aid = entry["article_id"]
        html = client.article(aid)
        parse_article(html, entry)  # Validate before replacing even an invalid cache.
        atomic_write(directory / "articles" / aid, html)
        LOGGER.info("Cached %s (%d/%d)", aid, index, len(pending))


def full_records(directory: Path, known: dict) -> dict:
    """Read the archive without extracting files or changing the historical artifact."""
    path = directory / "result.json"
    if path.exists():
        rows = read_json(path, [])
    elif (directory / "result-full.tgz").exists():
        with tarfile.open(directory / "result-full.tgz", "r:gz") as archive:
            with archive.extractfile("result.json") as handle:
                rows = json.load(handle)
    else:
        rows = []
    if not isinstance(rows, list):
        raise FormatError("Full corpus must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("article"), str):
            raise FormatError("Invalid full corpus record")
        result[article_id(row.get("id"))] = row
    for aid in sorted(set(known) - result.keys(), key=int):
        # A previous minimal-only update may already have published this ID.
        saved = known[aid]
        entry = {"article_id": aid, "title": saved["title"],
                 "input_date": saved["date"], "origin_name": saved["author"]}
        html = cached_article(directory, entry)
        if html is None:
            raise FormatError("--write-full needs historical HTML for {}; retain result-full.tgz and article caches".format(aid))
        result[aid] = dict(parse_article(html, entry), **saved)
    return result


def extract(directory, entries, known, write_full=False):
    additions = {}
    for aid, entry in entries.items():
        if aid in known:
            continue
        html = cached_article(directory, entry)
        if html is None:
            raise FormatError("Missing/invalid article {}; run the download stage first".format(aid))
        additions[aid] = parse_article(html, entry)
    # Validate and assemble everything before touching published outputs.
    merged = dict(known)
    for aid, record in additions.items():
        merged[aid] = {key: record[key] for key in ("title", "date", "author", "editor", "text")}
    if write_full:
        full = full_records(directory, known)
        # Existing full records are also add-only, even after a partially completed run.
        for aid, record in additions.items():
            full.setdefault(aid, record)
        write_json(directory / "result.json", list(full.values()))
    if additions or not (directory / "result-min.json").exists():
        write_json(directory / "result-min.json", merged)
    LOGGER.info("Published %d new articles; %d total (%d existing unchanged)",
                len(additions), len(merged), len(known))
    return merged


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
    parser.add_argument("stage", nargs="?", choices=("update", "entries", "download", "extract"),
                        default="update")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIR,
                        help="corpus/cache directory (default: directory containing this script)")
    parser.add_argument("--full-scan", action="store_true", help="scan all pages, including older gaps")
    parser.add_argument("--max-pages", type=positive_int, help="explicitly limit discovery (partial scan)")
    parser.add_argument("--delay", type=nonnegative_float, default=0.5,
                        help="minimum seconds between requests (default: 0.5)")
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--retries", type=nonnegative_int, default=3,
                        help="retries after transient errors; 0 disables retries (default: 3)")
    parser.add_argument("--log-level", type=str.upper,
                        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), default="INFO",
                        help="logging verbosity, written to stderr (default: INFO)")
    parser.add_argument("--write-full", action="store_true", help="also merge result.json, seeding from the archive")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    try:
        directory = args.data_dir.resolve()
        with corpus_lock(directory):
            known = load_corpus(directory)
            client = Client(args.delay, args.timeout, args.retries)
            if args.stage in ("update", "entries"):
                entries = discover(directory, client, set(known), args.full_scan, args.max_pages)
            else:
                if not (directory / "entries.json").exists():
                    raise FormatError("Missing entries.json; run the entries stage first")
                entries = load_entries(directory)
            if args.stage in ("update", "download"):
                download(directory, client, entries, known)
            if args.stage in ("update", "extract"):
                extract(directory, entries, known, args.write_full)
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted; validated caches are retained for the next run")
        return 130
    except (OSError, ValueError, tarfile.TarError, KeyError) as exc:
        LOGGER.error("Update failed: %s. Existing records were not removed; valid caches can be reused.",
                     exc, exc_info=LOGGER.isEnabledFor(logging.DEBUG))
        return 1


if __name__ == "__main__":
    sys.exit(main())
