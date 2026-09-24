import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update

FIXTURES = Path(__file__).parent / "fixtures"
CURRENT = (FIXTURES / "current.html").read_text(encoding="utf-8")
LEGACY = (FIXTURES / "legacy.html").read_text(encoding="utf-8")


def entry(aid):
    return {"article_id": str(aid), "title": " 标题 ", "input_date": "2026-09-24 08:28:01",
            "origin_name": "来源", "newcontent": "这是截断摘要..."}


def record(aid):
    return update.parse_article(LEGACY, entry(aid))


def minimal(aid):
    return {k: v for k, v in record(aid).items() if k not in ("id", "article")}


def listing(page, ids, total=8):
    return {"status": "success", "total": str(total), "curPage": page,
            "list": [entry(aid) for aid in ids]}


class FakeClient:
    def __init__(self, pages=None):
        self.pages = pages or {1: listing(1, [8, 7]), 2: listing(2, [6, 5]),
                               3: listing(3, [4, 3]), 4: listing(4, [2, 1])}
        self.listing_calls = []
        self.article_calls = []

    def listing(self, page):
        self.listing_calls.append(page)
        return self.pages[page]

    def article(self, aid):
        self.article_calls.append(aid)
        return CURRENT


class ParsingTests(unittest.TestCase):
    def test_current_nested_paragraphs_and_inline_tags(self):
        result = update.parse_article(CURRENT, entry(1))
        self.assertEqual(result["text"], ["第一段有内联标签。", "第二段。", "换行内容。",
                                          "《 人民日报 》（ 2026年09月24日 01 版）"])
        self.assertEqual(result["editor"], "(责编：测试)")
        self.assertEqual(result["title"], "标题")
        self.assertIn("<strong>", result["article"])
        self.assertNotIn("摘要", "".join(result["text"]))

    def test_legacy_newline_body_and_absent_editor(self):
        result = record(1)
        self.assertEqual(result["text"], ["旧版第一段。", "旧版第二段。"])
        self.assertEqual(result["editor"], "不明")

    def test_bad_body_is_not_published_as_text(self):
        for html in ("<h1>Access denied</h1>", '<div class="d2txt_con"><img src="a"></div>',
                     '<div class="d2txt_con"><script>error()</script></div>'):
            with self.subTest(html=html), self.assertRaises(update.FormatError):
                update.parse_article(html, entry(1))

    def test_listing_normalizes_integer_ids(self):
        payload = listing(1, [123], 1)
        payload["list"][0]["article_id"] = 123
        rows, total = update.parse_listing(payload, 1)
        self.assertEqual(rows[0]["article_id"], "123")
        self.assertEqual(total, 1)

    def test_unknown_listing_shapes_fail_closed(self):
        valid = listing(1, [1], 1)
        for changes in ({"status": "error"}, {"total": "oops"}, {"curPage": 2},
                        {"list": []}, {"list": {}}, {"list": [entry(1), entry(1)]}):
            with self.subTest(changes=changes), self.assertRaises(update.FormatError):
                update.parse_listing(dict(valid, **changes), 1)

    def test_empty_database(self):
        self.assertEqual(update.parse_listing(listing(1, [], 0), 1), ([], 0))

    def test_invalid_ids_and_missing_metadata(self):
        for value in ("../1", "a", None, True, 1.5):
            with self.subTest(value=value), self.assertRaises(update.FormatError):
                update.normalize_entry(dict(entry(1), article_id=value))
        with self.assertRaises(update.FormatError):
            update.normalize_entry({"article_id": "1"})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.known = {str(aid): minimal(aid) for aid in (3, 4, 5, 6, 99)}
        self.original = copy.deepcopy(self.known)
        update.write_json(self.directory / "result-min.json", self.known)
        self.original_bytes = (self.directory / "result-min.json").read_bytes()

    def test_incremental_refresh_and_idempotent_add_only_publish(self):
        # Old page-number caches must never hide newly inserted articles.
        update.write_json(self.directory / "api" / "1", [entry(99)])
        client = FakeClient()
        entries = update.discover(self.directory, client, set(self.known))
        self.assertEqual(client.listing_calls, [1, 2, 3])
        update.download(self.directory, client, entries, self.known)
        self.assertEqual(client.article_calls, ["8", "7"])
        result = update.extract(self.directory, entries, self.known)
        self.assertEqual(len(result), len(self.known) + 2)
        for aid, old in self.original.items():
            self.assertEqual(result[aid], old)
        self.assertEqual(self.known, self.original)
        self.assertIn("99", result)  # No longer listed upstream, never deleted locally.
        saved = (self.directory / "result-min.json").read_bytes()
        update.download(self.directory, client, entries, result)
        update.extract(self.directory, entries, result)
        self.assertEqual(client.article_calls, ["8", "7"])
        self.assertEqual((self.directory / "result-min.json").read_bytes(), saved)

    def test_full_scan_infers_page_size_and_exact_multiple(self):
        client = FakeClient()
        entries = update.discover(self.directory, client, set(self.known), full_scan=True)
        self.assertEqual(client.listing_calls, [1, 2, 3, 4])
        self.assertEqual(len(entries), 8)

    def test_last_partial_page_and_overlap_deduplication(self):
        client = FakeClient({1: listing(1, [8, 7], 5), 2: listing(2, [7, 6], 5),
                             3: listing(3, [5], 5)})
        entries = update.discover(self.directory, client, set(), full_scan=True)
        self.assertEqual(len(entries), 4)
        self.assertEqual(client.listing_calls, [1, 2, 3])

    def test_limit_is_explicit_and_old_entries_are_retained(self):
        update.write_json(self.directory / "entries.json", [entry(99)])
        client = FakeClient()
        entries = update.discover(self.directory, client, set(), max_pages=1)
        self.assertEqual(client.listing_calls, [1])
        self.assertEqual(set(entries), {"99", "8", "7"})

    def test_known_metadata_does_not_count_as_downloaded_article(self):
        update.write_json(self.directory / "entries.json", [entry(8), entry(7)])
        client = FakeClient()
        update.discover(self.directory, client, set(self.known))
        self.assertEqual(client.listing_calls, [1, 2, 3])

    def test_repeated_page_rejected_without_replacing_entries(self):
        path = self.directory / "entries.json"
        update.write_json(path, [entry(99)])
        before = path.read_bytes()
        client = FakeClient({1: listing(1, [8, 7]), 2: listing(2, [8, 7])})
        with self.assertRaises(update.FormatError):
            update.discover(self.directory, client, set())
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_cache_is_redownloaded(self):
        path = self.directory / "articles" / "8"
        update.atomic_write(path, "Access denied")
        client = FakeClient()
        with self.assertLogs(update.LOGGER, level="WARNING"):
            update.download(self.directory, client, {"8": entry(8)}, self.known)
        self.assertEqual(client.article_calls, ["8"])
        self.assertEqual(path.read_text(encoding="utf-8"), CURRENT)
        update.download(self.directory, client, {"8": entry(8)}, self.known)
        self.assertEqual(client.article_calls, ["8"])

    def test_failed_download_does_not_cache_error_page(self):
        with patch.object(FakeClient, "article", return_value="Blocked"):
            with self.assertRaises(update.FormatError):
                update.download(self.directory, FakeClient(), {"8": entry(8)}, self.known)
        self.assertFalse((self.directory / "articles" / "8").exists())
        self.assertEqual((self.directory / "result-min.json").read_bytes(), self.original_bytes)

    def test_missing_article_prevents_partial_publication(self):
        update.atomic_write(self.directory / "articles" / "8", CURRENT)
        with self.assertRaises(update.FormatError):
            update.extract(self.directory, {"8": entry(8), "7": entry(7)}, self.known)
        self.assertEqual((self.directory / "result-min.json").read_bytes(), self.original_bytes)

    def test_full_output_seeds_archive_without_mutating_it(self):
        archive_path = self.directory / "result-full.tgz"
        rows = [record(aid) for aid in self.known]
        payload = json.dumps(rows).encode()
        with tarfile.open(archive_path, "w:gz") as archive:
            info = tarfile.TarInfo("result.json")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        archive_bytes = archive_path.read_bytes()
        update.atomic_write(self.directory / "articles" / "8", CURRENT)
        update.extract(self.directory, {"8": entry(8)}, self.known, write_full=True)
        full = update.read_json(self.directory / "result.json", None)
        self.assertEqual(full[:-1], rows)
        self.assertEqual(full[-1]["id"], "8")
        self.assertEqual(archive_path.read_bytes(), archive_bytes)

    def test_full_output_missing_history_fails_before_publication(self):
        update.atomic_write(self.directory / "articles" / "8", CURRENT)
        with self.assertRaises(update.FormatError):
            update.extract(self.directory, {"8": entry(8)}, self.known, write_full=True)
        self.assertEqual((self.directory / "result-min.json").read_bytes(), self.original_bytes)

    def test_full_output_after_minimal_only_update_uses_cached_html(self):
        update.write_json(self.directory / "result.json", [record(aid) for aid in self.known])
        update.atomic_write(self.directory / "articles" / "8", CURRENT)
        known = update.extract(self.directory, {"8": entry(8)}, self.known)
        update.extract(self.directory, {"8": entry(8)}, known, write_full=True)
        full = {row["id"]: row for row in update.read_json(self.directory / "result.json", [])}
        self.assertEqual(set(full), set(known))
        self.assertEqual(full["8"]["text"], known["8"]["text"])

    def test_legacy_empty_text_and_author_are_preserved(self):
        self.known["99"]["text"] = []
        self.known["99"]["author"] = ""
        update.write_json(self.directory / "result-min.json", self.known)
        self.assertEqual(update.load_corpus(self.directory), self.known)
        update.atomic_write(self.directory / "articles" / "8", CURRENT)
        result = update.extract(self.directory, {"8": entry(8)}, self.known)
        self.assertEqual(result["99"], self.known["99"])

    def test_directory_lock_rejects_second_writer_and_releases_on_error(self):
        with self.assertRaises(RuntimeError):
            with update.corpus_lock(self.directory):
                with self.assertRaises(update.FormatError):
                    with update.corpus_lock(self.directory):
                        self.fail("Second writer acquired the lock")
                raise RuntimeError("interrupted")
        self.assertFalse((self.directory / ".update.lock").exists())
        with update.corpus_lock(self.directory):
            self.assertTrue((self.directory / ".update.lock").exists())

    def test_atomic_replace_failure_keeps_destination_and_cleans_temp(self):
        path = self.directory / "result-min.json"
        with patch.object(update.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                update.write_json(path, {})
        self.assertEqual(path.read_bytes(), self.original_bytes)
        self.assertFalse(list(self.directory.glob(".result-min.json*")))

    def test_malformed_local_corpus_is_not_treated_as_empty(self):
        update.write_json(self.directory / "result-min.json", [])
        with self.assertRaises(update.FormatError):
            update.load_corpus(self.directory)


class ClientAndCliTests(unittest.TestCase):
    @patch.object(update.time, "sleep")
    def test_retries_transient_http_errors_and_sets_headers(self, sleep):
        client = update.Client(delay=0, retries=1)
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get_content_charset.return_value = "utf-8"
        response.read.return_value = "中文".encode()
        error = HTTPError("url", 429, "Rate limited", {"Retry-After": "2"}, None)
        with patch.object(client.opener, "open", side_effect=[error, response]) as opened:
            with self.assertLogs(update.LOGGER, level="WARNING"):
                self.assertEqual(client.get("/article/1"), "中文")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://jhsjk.people.cn/article/1")
        self.assertEqual(request.get_header("Referer"), update.BASE_URL + "/")
        self.assertIsNotNone(request.get_header("User-agent"))
        sleep.assert_any_call(2.0)

    def test_permanent_http_error_is_not_retried(self):
        client = update.Client(delay=0)
        error = HTTPError("url", 404, "Missing", {}, None)
        with patch.object(client.opener, "open", side_effect=error) as opened:
            with self.assertRaises(HTTPError):
                client.article("1")
        self.assertEqual(opened.call_count, 1)

    @patch.object(update.time, "sleep")
    def test_retry_exhaustion_is_bounded(self, sleep):
        client = update.Client(delay=0, retries=2)
        with patch.object(client.opener, "open", side_effect=URLError("offline")) as opened:
            with self.assertLogs(update.LOGGER, level="WARNING"), self.assertRaises(URLError):
                client.article("1")
        self.assertEqual(opened.call_count, 3)

    def test_non_json_response_is_explicit_failure(self):
        with patch.object(update.Client, "get", return_value="<html>Blocked</html>"):
            with self.assertRaises(update.FormatError):
                update.Client().listing(1)

    def test_cli_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs(update.LOGGER, level="ERROR"):
                self.assertEqual(update.main(["extract", "--data-dir", directory]), 1)

    def test_canonical_cli_offers_help_without_network_or_cwd_dependency(self):
        command = Path(update.__file__)
        result = subprocess.run([sys.executable, str(command), "--help"], cwd="/",
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--log-level", result.stdout)

    def test_cli_extract_mode_and_case_insensitive_log_level(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            update.write_json(path / "entries.json", [entry(1)])
            update.atomic_write(path / "articles" / "1", LEGACY)
            self.assertEqual(update.main(["extract", "--data-dir", directory,
                                          "--log-level", "warning", "--retries", "0"]), 0)
            self.assertEqual(set(update.load_corpus(path)), {"1"})


if __name__ == "__main__":
    unittest.main()
