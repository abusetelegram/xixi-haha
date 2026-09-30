import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus


def full_record(aid="1", text=None):
    return {
        "id": str(aid),
        "title": "标题 " + str(aid),
        "date": "2026-09-24 08:28:01",
        "author": "来源",
        "editor": "编辑",
        "article": '<div class="d2txt_con"><p>正文</p></div>',
        "text": ["正文"] if text is None else text,
    }


def image_record(aid="40140589"):
    result = full_record(aid, text=[])
    result["article"] = ('<div class="d2txt_con"><img alt="" '
                         'src="https://example.invalid/article.jpg"/></div>')
    result["content_type"] = "image"
    result["media"] = [{
        "type": "image", "url": "https://example.invalid/article.jpg", "alt": ""}]
    return result


def minimal_record(record):
    return {field: record[field] for field in corpus.MINIMAL_FIELDS}


def write_legacy(directory, records, minimal=None, archive_json=None):
    directory = Path(directory)
    archive = directory / "result-full.tgz"
    payload = (archive_json.encode("utf-8") if archive_json is not None
               else json.dumps(records, ensure_ascii=False).encode("utf-8"))
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("result.json")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    minimal_path = directory / "result-min.json"
    if minimal is None:
        minimal = {str(record["id"]): minimal_record(record) for record in records}
    if isinstance(minimal, str):
        minimal_path.write_text(minimal, encoding="utf-8")
    else:
        minimal_path.write_text(json.dumps(minimal, ensure_ascii=False), encoding="utf-8")
    return archive, minimal_path


class RecordValidationTests(unittest.TestCase):
    def test_canonical_id_and_exact_schema(self):
        self.assertEqual(corpus.canonical_id("123"), "123")
        for value in (0, 1, "0", "01", "+1", "../1", True, None):
            with self.subTest(value=value), self.assertRaises(corpus.CorpusError):
                corpus.canonical_id(value)
        malformed = full_record()
        malformed["extra"] = "no"
        with self.assertRaisesRegex(corpus.CorpusError, "exactly"):
            corpus.canonical_record(malformed)

    def test_historical_empty_text_is_allowed_but_new_content_is_strict(self):
        historical = full_record(text=[])
        historical["title"] = ""
        self.assertEqual(corpus.canonical_record(historical)["text"], [])
        with self.assertRaises(corpus.CorpusError):
            corpus.canonical_record(historical, strict_content=True)

    def test_new_image_only_record_is_strictly_valid_and_deterministic(self):
        record = image_record()
        canonical = corpus.canonical_record(record, strict_content=True)
        self.assertEqual(canonical, record)
        self.assertEqual(list(canonical), list(corpus.BASE_FIELDS + corpus.MEDIA_FIELDS))
        self.assertEqual(corpus.minimal_record(record), {
            "title": record["title"], "date": record["date"],
            "author": record["author"], "editor": record["editor"], "text": [],
            "content_type": "image", "media": record["media"],
        })
        self.assertEqual(corpus.serialize_record(record, strict_content=True),
                         corpus.serialize_record(record, strict_content=True))

    def test_media_marker_shape_and_urls_fail_closed(self):
        valid = image_record()
        mutations = []
        missing_media = dict(valid)
        del missing_media["media"]
        mutations.append(missing_media)
        missing_marker = dict(valid)
        del missing_marker["content_type"]
        mutations.append(missing_marker)
        wrong_marker = dict(valid, content_type="video")
        mutations.append(wrong_marker)
        with_text = dict(valid, text=["not image-only"])
        mutations.append(with_text)
        empty_media = dict(valid, media=[])
        mutations.append(empty_media)
        no_body_evidence = dict(valid, article="<div>metadata only</div>")
        mutations.append(no_body_evidence)
        wrong_item = dict(valid, media=[{"type": "video", "url": "https://example/a", "alt": ""}])
        mutations.append(wrong_item)
        wrong_alt = dict(valid, media=[{"type": "image", "url": "https://example/a", "alt": 1}])
        mutations.append(wrong_alt)
        for url in ("", "/relative.jpg", "data:image/png,abc", "javascript:alert(1)",
                    "https://user@example.invalid/a.jpg", "https://example.invalid/a b.jpg"):
            mutations.append(dict(valid, media=[{"type": "image", "url": url, "alt": ""}]))
        for record in mutations:
            with self.subTest(record=record), self.assertRaises(corpus.CorpusError):
                corpus.canonical_record(record, strict_content=True)

    def test_schema_describes_paired_image_extension(self):
        schema = json.loads((corpus.TEMPLATE_DIR / "schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["content_type"], {"const": "image"})
        media = schema["properties"]["media"]
        self.assertEqual(media["minItems"], 1)
        self.assertEqual(set(media["items"]["required"]), {"type", "url", "alt"})
        self.assertEqual(schema["oneOf"][1]["properties"]["text"]["maxItems"], 0)

    def test_serialization_is_fixed_utf8_pretty_json_with_lf(self):
        payload = corpus.serialize_record(full_record())
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b"\r", payload)
        self.assertIn("标题".encode("utf-8"), payload)
        self.assertEqual(list(json.loads(payload)), list(corpus.FIELDS))


class CanonicalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_create_is_add_only_locked_and_idempotent(self):
        records = [full_record("2"), full_record("1")]
        self.assertEqual(corpus.create_articles(self.directory, records), 2)
        before = {path.name: path.read_bytes() for path in (self.directory / "articles").iterdir()}
        self.assertEqual(corpus.create_articles(self.directory, records), 0)
        self.assertEqual(corpus.load_articles(self.directory), {
            "1": full_record("1"), "2": full_record("2")})
        self.assertEqual(before, {path.name: path.read_bytes()
                                  for path in (self.directory / "articles").iterdir()})
        changed = full_record("1")
        changed["title"] = "changed"
        with self.assertRaisesRegex(corpus.CorpusError, "Refusing to modify"):
            corpus.create_articles(self.directory, [changed])
        self.assertEqual(before["1.json"], (self.directory / "articles" / "1.json").read_bytes())
        self.assertFalse((self.directory / ".update.lock").exists())

    def test_batch_duplicate_and_lock_conflict_fail_before_mutation(self):
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate normalized"):
            corpus.create_articles(self.directory, [full_record("1"), full_record("1")])
        self.assertFalse((self.directory / "articles").exists())
        with corpus.writer_lock(self.directory):
            with self.assertRaisesRegex(corpus.CorpusError, "another writer"):
                corpus.create_articles(self.directory, [full_record("1")])
        self.assertFalse((self.directory / "articles").exists())

    def test_publish_failure_never_changes_existing_file(self):
        corpus.create_articles(self.directory, [full_record("1")])
        existing = (self.directory / "articles" / "1.json").read_bytes()
        with patch.object(corpus.os, "link", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                corpus.create_articles(self.directory, [full_record("2")])
        self.assertEqual((self.directory / "articles" / "1.json").read_bytes(), existing)
        self.assertFalse((self.directory / "articles" / "2.json").exists())

    def test_loader_rejects_filename_mismatch_nondeterminism_and_duplicate_key(self):
        articles = self.directory / "articles"
        articles.mkdir()
        (articles / "2.json").write_bytes(corpus.serialize_record(full_record("1")))
        with self.assertRaisesRegex(corpus.CorpusError, "Filename/record ID mismatch"):
            corpus.load_articles(self.directory)
        (articles / "2.json").unlink()
        compact = json.dumps(full_record("1"), ensure_ascii=False).encode("utf-8")
        (articles / "1.json").write_bytes(compact)
        with self.assertRaisesRegex(corpus.CorpusError, "deterministic"):
            corpus.load_articles(self.directory)
        (articles / "1.json").write_text(
            '{"id":"1","id":"1","title":"t","date":"d","author":"a",'
            '"editor":"e","article":"h","text":["x"]}\n', encoding="utf-8")
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate JSON object key"):
            corpus.load_articles(self.directory)

    def test_loader_rejects_normalized_filename_aliases(self):
        articles = self.directory / "articles"
        articles.mkdir()
        (articles / "1.json").write_bytes(corpus.serialize_record(full_record("1")))
        (articles / "01.json").write_bytes(corpus.serialize_record(full_record("1")))
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate normalized article filenames"):
            corpus.load_articles(self.directory)

    def test_loader_accepts_absent_or_real_articles_directory(self):
        self.assertEqual(corpus.load_articles(self.directory), {})
        (self.directory / "articles").mkdir()
        self.assertEqual(corpus.load_articles(self.directory), {})

    def test_loader_rejects_existing_and_dangling_articles_symlinks(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "marker").write_text("unchanged", encoding="utf-8")
        for name, target in (("existing", outside),
                             ("dangling", self.directory / "missing")):
            root = self.directory / name
            root.mkdir()
            (root / "articles").symlink_to(target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaisesRegex(
                    corpus.CorpusError, "must be a real directory"):
                corpus.load_articles(root)
        self.assertEqual((outside / "marker").read_text(encoding="utf-8"), "unchanged")

    def test_initializer_accepts_absent_or_real_articles_directory(self):
        absent = self.directory / "absent"
        self.assertEqual(corpus.initialize_data_root(absent), 4)
        self.assertTrue((absent / "articles").is_dir())
        real = self.directory / "real"
        (real / "articles").mkdir(parents=True)
        self.assertEqual(corpus.initialize_data_root(real), 4)
        self.assertTrue((real / "articles").is_dir())

    def test_initializer_rejects_articles_symlinks_before_template_publication(self):
        outside = self.directory / "outside-init"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_text("unchanged", encoding="utf-8")
        for name, target in (("existing-init", outside),
                             ("dangling-init", self.directory / "missing-init")):
            root = self.directory / name
            root.mkdir()
            (root / "articles").symlink_to(target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaisesRegex(
                    corpus.CorpusError, "must be a real directory"):
                corpus.initialize_data_root(root)
            for template in ("README.md", "schema.json", ".gitignore", "index.html"):
                self.assertFalse((root / template).exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.target = self.base / "target"
        self.source.mkdir()

    def test_import_validates_projection_and_is_exact_idempotent(self):
        records = [full_record("2", text=[]), full_record("1")]
        archive, minimal = write_legacy(self.source, records)
        summary = corpus.import_legacy(archive, minimal, self.target)
        self.assertEqual(summary, {"records": 2, "added": 2, "templates_added": 4})
        first = {path.relative_to(self.target): path.read_bytes()
                 for path in self.target.rglob("*") if path.is_file()}
        self.assertEqual(corpus.import_legacy(archive, minimal, self.target),
                         {"records": 2, "added": 0, "templates_added": 0})
        second = {path.relative_to(self.target): path.read_bytes()
                  for path in self.target.rglob("*") if path.is_file()}
        self.assertEqual(first, second)
        self.assertEqual(corpus.validate_import(self.target, archive, minimal),
                         {"1": full_record("1"), "2": full_record("2", text=[])})
        for name in ("README.md", "schema.json", ".gitignore", "index.html"):
            self.assertTrue((self.target / name).is_file())

    def test_projection_mismatch_fails_before_target_mutation(self):
        records = [full_record("1")]
        wrong = {"1": minimal_record(records[0])}
        wrong["1"]["title"] = "wrong"
        archive, minimal = write_legacy(self.source, records, wrong)
        with self.assertRaisesRegex(corpus.CorpusError, "projection mismatch"):
            corpus.import_legacy(archive, minimal, self.target)
        self.assertFalse(self.target.exists())

    def test_normalized_full_id_duplicates_fail_before_target_mutation(self):
        first = full_record("1")
        alias = full_record("1")
        alias["id"] = 1
        archive, minimal = write_legacy(self.source, [first, alias], {"1": minimal_record(first)})
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate normalized full"):
            corpus.import_legacy(archive, minimal, self.target)
        self.assertFalse(self.target.exists())

    def test_duplicate_json_keys_fail_before_target_mutation(self):
        row = ('{"id":"1","id":"1","title":"t","date":"d","author":"a",'
               '"editor":"e","article":"h","text":["x"]}')
        archive, minimal = write_legacy(
            self.source, [], minimal='{"1":{"title":"t","date":"d","author":"a",'
            '"editor":"e","text":["x"]}}', archive_json="[{}]".format(row))
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate JSON object key"):
            corpus.import_legacy(archive, minimal, self.target)
        self.assertFalse(self.target.exists())

    def test_minimal_normalized_alias_and_unsafe_archive_are_rejected(self):
        record = full_record("1")
        alias_minimal = ('{"1":' + json.dumps(minimal_record(record), ensure_ascii=False)
                         + ',"01":' + json.dumps(minimal_record(record), ensure_ascii=False) + '}')
        archive, minimal = write_legacy(self.source, [record], alias_minimal)
        with self.assertRaisesRegex(corpus.CorpusError, "Duplicate normalized minimal"):
            corpus.load_legacy(archive, minimal)

        unsafe = self.source / "unsafe.tgz"
        payload = b"[]"
        with tarfile.open(unsafe, "w:gz") as handle:
            info = tarfile.TarInfo("../result.json")
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(corpus.CorpusError, "only the regular file"):
            corpus.load_legacy(unsafe, minimal)

    def test_import_rejects_articles_symlinks_before_publication(self):
        archive, minimal = write_legacy(self.source, [full_record("1")])
        outside = self.base / "outside"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_text("unchanged", encoding="utf-8")
        for name, link_target in (("existing-link", outside),
                                  ("dangling-link", self.base / "missing")):
            target = self.base / name
            target.mkdir()
            (target / "articles").symlink_to(link_target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaisesRegex(
                    corpus.CorpusError, "must be a real directory"):
                corpus.import_legacy(archive, minimal, target)
            for template in ("README.md", "schema.json", ".gitignore", "index.html"):
                self.assertFalse((target / template).exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_cli_import_and_validate(self):
        archive, minimal = write_legacy(self.source, [full_record("1")])
        script = Path(corpus.__file__)
        imported = subprocess.run(
            [sys.executable, str(script), "import-legacy", "--archive", str(archive),
             "--minimal", str(minimal), "--data-dir", str(self.target)],
            cwd="/", capture_output=True, text=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["records"], 1)
        validated = subprocess.run(
            [sys.executable, str(script), "validate", "--archive", str(archive),
             "--minimal", str(minimal), "--data-dir", str(self.target)],
            cwd="/", capture_output=True, text=True)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout), {"empty_text": 0, "records": 1})


if __name__ == "__main__":
    unittest.main()
