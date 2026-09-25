import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import update

FIXTURES = Path(__file__).parent / "fixtures"
CURRENT = (FIXTURES / "current.html").read_text(encoding="utf-8")
LEGACY = (FIXTURES / "legacy.html").read_text(encoding="utf-8")


def entry(aid, title=" 标题 "):
    return {"article_id": str(aid), "title": title,
            "input_date": "2026-09-24 08:28:01", "origin_name": "来源",
            "newcontent": "ignored preview"}


def record(aid, html=LEGACY):
    return update.parse_article(html, entry(aid))


def listing(page, ids, total=8):
    return {"status": "success", "total": str(total), "curPage": page,
            "list": [entry(aid) for aid in ids]}


class FakeClient:
    def __init__(self, pages=None, articles=None):
        self.pages = pages or {1: listing(1, [8, 7]), 2: listing(2, [6, 5]),
                               3: listing(3, [4, 3]), 4: listing(4, [2, 1])}
        self.articles = articles or {}
        self.listing_calls = []
        self.article_calls = []

    def listing(self, page):
        self.listing_calls.append(page)
        value = self.pages[page]
        if isinstance(value, BaseException):
            raise value
        return value

    def article(self, aid):
        self.article_calls.append(aid)
        value = self.articles.get(aid, CURRENT)
        if isinstance(value, BaseException):
            raise value
        return value


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

    def test_bad_body_is_not_accepted(self):
        for html in ("<h1>Access denied</h1>", '<div class="d2txt_con"><img src="a"></div>',
                     '<div class="d2txt_con"><script>error()</script></div>'):
            with self.subTest(html=html), self.assertRaises(update.FormatError):
                update.parse_article(html, entry(1))

    def test_listing_normalizes_integer_and_leading_zero_ids(self):
        payload = listing(1, [123], 1)
        payload["list"][0]["article_id"] = 123
        rows, total = update.parse_listing(payload, 1)
        self.assertEqual(rows[0]["article_id"], "123")
        self.assertEqual(set(rows[0]), {"article_id", "title", "input_date", "origin_name"})
        self.assertEqual(total, 1)
        payload["list"][0]["article_id"] = "00123"
        rows, _ = update.parse_listing(payload, 1)
        self.assertEqual(rows[0]["article_id"], "123")

    def test_null_author_normalizes_through_listing_and_record_generation(self):
        payload = listing(331, [32342100], 15091)
        payload["list"][0]["origin_name"] = None
        rows, total = update.parse_listing(payload, 331)
        self.assertEqual(total, 15091)
        self.assertEqual(rows[0]["origin_name"], "")
        self.assertEqual(update.parse_article(LEGACY, rows[0])["author"], "")

    def test_empty_author_is_preserved(self):
        row = entry(32342100)
        row["origin_name"] = ""
        self.assertEqual(update.normalize_entry(row)["origin_name"], "")

    def test_missing_and_wrong_type_author_remain_invalid(self):
        missing = entry(32342100)
        del missing["origin_name"]
        with self.assertRaisesRegex(update.FormatError, "missing origin_name"):
            update.normalize_entry(missing)
        for value in (False, 0, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(
                    update.FormatError, "invalid origin_name"):
                update.normalize_entry(dict(entry(32342100), origin_name=value))

    def test_unknown_listing_shapes_fail_closed(self):
        valid = listing(1, [1], 1)
        for changes in ({"status": "error"}, {"total": "oops"}, {"curPage": 2},
                        {"list": []}, {"list": {}}, {"list": [entry(1), entry(1)]}):
            with self.subTest(changes=changes), self.assertRaises(update.FormatError):
                update.parse_listing(dict(valid, **changes), 1)

    def test_empty_database(self):
        self.assertEqual(update.parse_listing(listing(1, [], 0), 1), ([], 0))

    def test_invalid_ids_and_missing_metadata(self):
        for value in ("../1", "a", None, True, 1.5, 0, "0"):
            with self.subTest(value=value), self.assertRaises(update.FormatError):
                update.normalize_entry(dict(entry(1), article_id=value))
        with self.assertRaises(update.FormatError):
            update.normalize_entry({"article_id": "1"})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.known_ids = (3, 4, 5, 6, 99)
        corpus.create_articles(self.directory, [record(aid) for aid in self.known_ids])
        self.original = {path.name: path.read_bytes()
                         for path in (self.directory / "articles").iterdir()}

    def assert_existing_unchanged(self):
        for name, content in self.original.items():
            self.assertEqual((self.directory / "articles" / name).read_bytes(), content)

    def test_incremental_update_stops_after_two_known_pages_and_is_idempotent(self):
        client = FakeClient()
        summary, code = update.run_update(self.directory, client)
        self.assertEqual(code, 0)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["stop_reason"], "two_known_pages")
        self.assertEqual(summary["pages"], 3)
        self.assertEqual(summary["missing"], 2)
        self.assertEqual(summary["added"], 2)
        self.assertEqual(client.listing_calls, [1, 2, 3])
        self.assertEqual(client.article_calls, ["8", "7"])
        self.assertEqual(set(corpus.load_articles(self.directory)),
                         {"3", "4", "5", "6", "7", "8", "99"})
        self.assert_existing_unchanged()

        second = FakeClient()
        again, code = update.run_update(self.directory, second)
        self.assertEqual(code, 0)
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["downloaded"], 0)
        self.assertEqual(second.article_calls, [])
        self.assert_existing_unchanged()

    def test_incremental_resume_retains_backlog_past_known_leading_pages(self):
        # A prior complete discovery retained IDs 1-10.  A partial local
        # publication made the newest pages canonical, while older IDs 1 and 2
        # remain pending and ID 2 already has validated HTML cached.
        corpus.create_articles(
            self.directory, [record(aid) for aid in (7, 8, 9, 10)])
        self.original = {path.name: path.read_bytes()
                         for path in (self.directory / "articles").iterdir()}
        update.write_json(
            update.state_path(self.directory), [entry(aid) for aid in range(1, 11)])
        cached_path = update.html_cache_path(self.directory, "2")
        update.atomic_write(cached_path, CURRENT)
        cached_before = cached_path.read_bytes()
        pages = {
            1: listing(1, [10, 9], 10),
            2: listing(2, [8, 7], 10),
        }
        client = FakeClient(pages)

        summary, code = update.run_update(self.directory, client)

        self.assertEqual(code, 0)
        self.assertEqual(summary["stop_reason"], "two_known_pages")
        self.assertEqual(summary["missing"], 2)
        self.assertEqual(summary["added"], 2)
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(summary["cache_hits"], 1)
        self.assertEqual(client.listing_calls, [1, 2])
        self.assertEqual(client.article_calls, ["1"])
        self.assertEqual(cached_path.read_bytes(), cached_before)
        self.assert_existing_unchanged()
        self.assertEqual(set(corpus.load_articles(self.directory)),
                         {str(aid) for aid in range(1, 11)} | {"99"})

    def test_full_scan_lists_every_page_but_fetches_only_missing(self):
        client = FakeClient()
        summary, code = update.run_update(self.directory, client, full_scan=True)
        self.assertEqual(code, 0)
        self.assertEqual(summary["stop_reason"], "listing_exhausted")
        self.assertEqual(summary["pages"], 4)
        self.assertEqual(client.listing_calls, [1, 2, 3, 4])
        self.assertEqual(client.article_calls, ["8", "7", "2", "1"])
        self.assertEqual(summary["added"], 4)

    def test_variable_nonfinal_page_size_fails_closed_without_state_replacement(self):
        update.write_json(update.state_path(self.directory), [entry(99)])
        before = update.state_path(self.directory).read_bytes()
        pages = {
            1: listing(1, range(25, 15, -1), 25),
            2: listing(2, range(15, 10, -1), 25),
            3: listing(3, range(10, 5, -1), 25),
        }
        client = FakeClient(pages)
        with self.assertRaisesRegex(update.FormatError, "page size changed"):
            update.discover(self.directory, client, set(), full_scan=True)
        self.assertEqual(client.listing_calls, [1, 2])
        self.assertEqual(update.state_path(self.directory).read_bytes(), before)

    def test_cross_page_overlap_is_counted_and_deduplicated(self):
        pages = {1: listing(1, [8, 7], 5), 2: listing(2, [7, 6], 5),
                 3: listing(3, [5], 5)}
        result = update.discover(self.directory, FakeClient(pages), set(), full_scan=True)
        self.assertEqual(result["listed"], 4)
        self.assertEqual(result["overlaps"], 1)
        self.assertTrue(result["complete"])

    def test_conflicting_cross_page_metadata_fails(self):
        second = listing(2, [7, 6], 4)
        second["list"][0] = entry(7, title="different")
        pages = {1: listing(1, [8, 7], 4), 2: second}
        with self.assertRaisesRegex(update.FormatError, "Conflicting listing metadata"):
            update.discover(self.directory, FakeClient(pages), set(), full_scan=True)

    def test_repeated_page_and_moving_total_fail_without_state_replacement(self):
        update.write_json(update.state_path(self.directory), [entry(99)])
        before = update.state_path(self.directory).read_bytes()
        cases = [
            {1: listing(1, [8, 7]), 2: listing(2, [8, 7])},
            {1: listing(1, [8, 7], 8), 2: listing(2, [6, 5], 9)},
        ]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(update.FormatError):
                update.discover(self.directory, FakeClient(pages), set(), full_scan=True)
            self.assertEqual(update.state_path(self.directory).read_bytes(), before)

    def test_page_limit_precedence_around_incremental_stop(self):
        known = {"3", "4", "5", "6", "99"}
        cases = (
            (2, False, "page_limit", [1, 2]),
            (3, False, "page_limit", [1, 2, 3]),
            (4, True, "two_known_pages", [1, 2, 3]),
        )
        for cap, complete, reason, calls in cases:
            with self.subTest(cap=cap):
                client = FakeClient()
                result = update.discover(
                    self.directory, client, known, max_pages=cap)
                self.assertEqual(result["complete"], complete)
                self.assertEqual(result["stop_reason"], reason)
                self.assertEqual(client.listing_calls, calls)

    def test_natural_exhaustion_at_page_limit_is_complete(self):
        pages = {
            1: listing(1, [8, 7], 6),
            2: listing(2, [6, 5], 6),
            3: listing(3, [4, 3], 6),
        }
        client = FakeClient(pages)
        result = update.discover(
            self.directory, client, {"3", "4", "5", "6"}, max_pages=3)
        self.assertTrue(result["complete"])
        self.assertEqual(result["stop_reason"], "listing_exhausted")
        self.assertEqual(client.listing_calls, [1, 2, 3])

    def test_nonpositive_programmatic_page_limit_is_rejected_before_listing(self):
        client = FakeClient()
        for cap in (0, -1, False):
            with self.subTest(cap=cap), self.assertRaisesRegex(
                    update.FormatError, "positive integer"):
                update.discover(self.directory, client, set(), max_pages=cap)
        self.assertEqual(client.listing_calls, [])

    def test_page_limit_is_explicit_incomplete_and_does_not_publish(self):
        client = FakeClient()
        summary, code = update.run_update(self.directory, client, max_pages=1)
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "incomplete")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["stop_reason"], "page_limit")
        self.assertEqual(summary["pages"], 1)
        self.assertEqual(client.article_calls, [])
        self.assert_existing_unchanged()

    def test_addition_limit_is_explicit_incomplete_before_download(self):
        client = FakeClient()
        summary, code = update.run_update(self.directory, client, max_additions=1)
        self.assertEqual(code, 1)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["stop_reason"], "addition_limit")
        self.assertEqual(summary["missing"], 2)
        self.assertEqual(client.article_calls, [])
        self.assert_existing_unchanged()

    def test_invalid_cache_is_refetched_outside_canonical_articles(self):
        path = update.html_cache_path(self.directory, "8")
        update.atomic_write(path, "Access denied")
        client = FakeClient()
        with self.assertLogs(update.LOGGER, level="WARNING"):
            result = update.fetch_missing(self.directory, client, {"8": entry(8)}, {"3"})
        self.assertEqual(client.article_calls, ["8"])
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(path.read_text(encoding="utf-8"), CURRENT)
        self.assertFalse((self.directory / "articles" / "8").exists())

    def test_updater_lock_precedes_discovery_and_releases_on_success_and_failure(self):
        blocked = FakeClient()
        with corpus.writer_lock(self.directory):
            with self.assertRaisesRegex(corpus.CorpusError, "another writer"):
                update.run_update(self.directory, blocked)
        self.assertEqual(blocked.listing_calls, [])
        self.assertFalse(update.state_path(self.directory).exists())
        self.assertFalse((self.directory / ".update.lock").exists())

        successful = FakeClient()
        summary, code = update.run_update(self.directory, successful)
        self.assertEqual((code, summary["status"]), (0, "success"))
        self.assertFalse((self.directory / ".update.lock").exists())

        interrupted = FakeClient({1: KeyboardInterrupt()})
        with self.assertRaises(KeyboardInterrupt):
            update.run_update(self.directory, interrupted)
        self.assertFalse((self.directory / ".update.lock").exists())

    def test_network_failure_after_one_download_publishes_nothing_but_keeps_cache(self):
        client = FakeClient(articles={"7": URLError("offline")})
        with self.assertRaises(URLError):
            update.run_update(self.directory, client)
        self.assertTrue(update.html_cache_path(self.directory, "8").exists())
        self.assertFalse(update.html_cache_path(self.directory, "7").exists())
        self.assertFalse((self.directory / "articles" / "8.json").exists())
        self.assert_existing_unchanged()
        self.assertFalse((self.directory / ".update.lock").exists())

    def test_malformed_article_after_valid_download_publishes_nothing(self):
        client = FakeClient(articles={"7": "<h1>Blocked</h1>"})
        with self.assertRaises(update.FormatError):
            update.run_update(self.directory, client)
        self.assertTrue(update.html_cache_path(self.directory, "8").exists())
        self.assertFalse((self.directory / "articles" / "8.json").exists())
        self.assert_existing_unchanged()

    def test_state_is_ignored_and_duplicate_checked(self):
        update.write_json(update.state_path(self.directory), [entry(8), entry("08")])
        with self.assertRaisesRegex(update.FormatError, "Duplicate article ID 8"):
            update.load_entries(self.directory)
        self.assertFalse((self.directory / "entries.json").exists())

    def test_atomic_replace_failure_keeps_cache_destination(self):
        path = update.state_path(self.directory)
        update.write_json(path, [entry(1)])
        before = path.read_bytes()
        with patch.object(update.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                update.write_json(path, [])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob(".entries.json*")))


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

    def test_cli_requires_explicit_data_dir(self):
        with self.assertRaises(SystemExit) as raised:
            update.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_zero_page_limit(self):
        with self.assertRaises(SystemExit) as raised:
            update.main(["--data-dir", "/tmp/unused", "--max-pages", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_emits_machine_readable_success_and_incomplete_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient({1: listing(1, [], 0)})
            stdout = io.StringIO()
            with patch.object(update, "Client", return_value=client), patch("sys.stdout", stdout):
                code = update.main(["--data-dir", directory, "--max-pages", "1"])
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["status"], "success")
            self.assertTrue(report["complete"])
            self.assertEqual(report["pages"], 1)
            self.assertEqual(report["records"], 0)

    def test_cli_help_has_no_cwd_dependency(self):
        command = Path(update.__file__)
        result = subprocess.run([sys.executable, str(command), "--help"], cwd="/",
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--max-additions", result.stdout)
        self.assertIn("--data-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
