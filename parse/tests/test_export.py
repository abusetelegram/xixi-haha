import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import export as exporter


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


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / "data"

    def test_exports_are_byte_identical_numeric_ordered_and_fixed_metadata(self):
        corpus.create_articles(
            self.data, [full_record("10"), full_record("2"), full_record("1", text=[])],
            strict_content=False)
        first = self.base / "first"
        second = self.base / "second"
        options = dict(include_full=True, include_archive=True,
                       code_sha="a" * 40, data_sha="b" * 40)
        summary = exporter.export_corpus(self.data, first, **options)
        exporter.export_corpus(self.data, second, **options)
        # Re-exporting to an existing real output directory remains supported.
        exporter.export_corpus(self.data, first, **options)

        names = [exporter.MINIMAL_NAME, exporter.FULL_NAME,
                 exporter.ARCHIVE_NAME, exporter.PROVENANCE_NAME]
        for name in names:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

        minimal = json.loads((first / exporter.MINIMAL_NAME).read_text(encoding="utf-8"))
        self.assertEqual(list(minimal), ["1", "2", "10"])
        self.assertEqual(minimal["1"]["text"], [])
        self.assertEqual(set(minimal["1"]), set(corpus.MINIMAL_FIELDS))
        self.assertEqual(set(minimal), {"1", "2", "10"})

        full_bytes = (first / exporter.FULL_NAME).read_bytes()
        self.assertEqual([row["id"] for row in json.loads(full_bytes)], ["1", "2", "10"])
        archive_bytes = (first / exporter.ARCHIVE_NAME).read_bytes()
        self.assertEqual(archive_bytes[:4], b"\x1f\x8b\x08\x00")
        self.assertEqual(archive_bytes[4:8], b"\x00\x00\x00\x00")
        with tarfile.open(first / exporter.ARCHIVE_NAME, "r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(member.name, exporter.FULL_NAME)
            self.assertEqual(member.mtime, 0)
            self.assertEqual(member.mode, 0o644)
            self.assertEqual((member.uid, member.gid, member.uname, member.gname),
                             (0, 0, "", ""))
            self.assertEqual(archive.extractfile(member).read(), full_bytes)

        provenance = json.loads(
            (first / exporter.PROVENANCE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(provenance["code_sha"], "a" * 40)
        self.assertEqual(provenance["data_sha"], "b" * 40)
        self.assertEqual(provenance["records"], 3)
        self.assertEqual(provenance["artifacts"], summary["artifacts"])
        self.assertEqual(set(provenance["artifacts"]),
                         {exporter.MINIMAL_NAME, exporter.FULL_NAME,
                          exporter.ARCHIVE_NAME})
        for name, metadata in provenance["artifacts"].items():
            payload = (first / name).read_bytes()
            self.assertEqual(metadata,
                             {"sha256": hashlib.sha256(payload).hexdigest(),
                              "bytes": len(payload)})

    def test_schema_and_filename_failures_leave_existing_output_untouched(self):
        for failure in ("schema", "filename"):
            with self.subTest(failure=failure):
                data = self.base / ("data-" + failure)
                articles = data / "articles"
                articles.mkdir(parents=True)
                if failure == "schema":
                    malformed = full_record("1")
                    malformed["extra"] = "not allowed"
                    (articles / "1.json").write_text(
                        json.dumps(malformed, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
                else:
                    (articles / "not-an-id.json").write_bytes(
                        corpus.serialize_record(full_record("1")))
                output = self.base / ("output-" + failure)
                output.mkdir()
                sentinel = output / exporter.MINIMAL_NAME
                sentinel.write_bytes(b"do not replace\n")

                with self.assertRaises(corpus.CorpusError):
                    exporter.export_corpus(data, output, include_full=True,
                                           include_archive=True)
                self.assertEqual(sentinel.read_bytes(), b"do not replace\n")
                self.assertEqual(list(output.iterdir()), [sentinel])

    def test_lexically_internal_output_symlink_is_rejected_without_changes(self):
        corpus.create_articles(self.data, [full_record("1")])
        target = self.base / "external-target"
        target.mkdir()
        sentinel = target / exporter.MINIMAL_NAME
        sentinel.write_bytes(b"keep me\n")
        output = self.data / "downloads"
        output.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(exporter.ExportError, "outside"):
            exporter.export_corpus(self.data, output)

        self.assertTrue(output.is_symlink())
        self.assertEqual(output.readlink(), target)
        self.assertEqual(sentinel.read_bytes(), b"keep me\n")
        self.assertEqual(list(target.iterdir()), [sentinel])

    def test_external_output_symlinks_and_symlink_ancestors_are_rejected(self):
        corpus.create_articles(self.data, [full_record("1")])
        target = self.base / "target"
        target.mkdir()
        sentinel = target / exporter.MINIMAL_NAME
        sentinel.write_bytes(b"keep me\n")

        output_link = self.base / "output-link"
        output_link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(exporter.ExportError, "symlinks"):
            exporter.export_corpus(self.data, output_link)
        self.assertTrue(output_link.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b"keep me\n")

        ancestor_link = self.base / "ancestor-link"
        ancestor_link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(exporter.ExportError, "symlinks"):
            exporter.export_corpus(self.data, ancestor_link / "nested")
        self.assertTrue(ancestor_link.is_symlink())
        self.assertFalse((target / "nested").exists())
        self.assertEqual(sentinel.read_bytes(), b"keep me\n")

    def test_output_equal_to_common_live_symlink_preserves_target(self):
        target = self.base / "target"
        data = target / "data"
        corpus.create_articles(data, [full_record("1")])
        sentinel = target / exporter.MINIMAL_NAME
        sentinel.write_bytes(b"keep me\n")
        output = self.base / "output-link"
        output.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(exporter.ExportError, "symlinks"):
            exporter.export_corpus(output / "data", output)

        self.assertTrue(output.is_symlink())
        self.assertEqual(output.readlink(), target)
        self.assertEqual(sentinel.read_bytes(), b"keep me\n")

    def test_output_equal_to_common_dangling_symlink_preserves_link(self):
        missing_target = self.base / "missing-target"
        output = self.base / "dangling-output"
        output.symlink_to(missing_target, target_is_directory=True)

        with self.assertRaisesRegex(exporter.ExportError, "symlinks"):
            exporter.export_corpus(output / "data", output)

        self.assertTrue(output.is_symlink())
        self.assertEqual(output.readlink(), missing_target)
        self.assertFalse(missing_target.exists())

    def test_dangling_output_symlink_is_rejected_without_replacing_link(self):
        corpus.create_articles(self.data, [full_record("1")])
        missing_target = self.base / "missing-target"
        output = self.base / "dangling-output"
        output.symlink_to(missing_target, target_is_directory=True)

        with self.assertRaisesRegex(exporter.ExportError, "symlinks"):
            exporter.export_corpus(self.data, output)

        self.assertTrue(output.is_symlink())
        self.assertEqual(output.readlink(), missing_target)
        self.assertFalse(missing_target.exists())

    def test_resolved_output_inside_real_data_checkout_is_rejected(self):
        real_data = self.base / "real-data"
        corpus.create_articles(real_data, [full_record("1")])
        data_alias = self.base / "data-alias"
        data_alias.symlink_to(real_data, target_is_directory=True)
        output = real_data / "downloads"

        with self.assertRaisesRegex(exporter.ExportError, "outside"):
            exporter.export_corpus(data_alias, output)

        self.assertFalse(output.exists())

    def test_output_inside_canonical_checkout_and_incomplete_sha_are_rejected(self):
        corpus.create_articles(self.data, [full_record("1")])
        with self.assertRaisesRegex(exporter.ExportError, "outside"):
            exporter.export_corpus(self.data, self.data / "downloads")
        self.assertFalse((self.data / "downloads").exists())
        output = self.base / "output"
        with self.assertRaisesRegex(exporter.ExportError, "supplied together"):
            exporter.export_corpus(self.data, output, code_sha="a" * 40)
        self.assertFalse(output.exists())

    def test_cli_writes_minimal_projection_and_machine_summary(self):
        corpus.create_articles(self.data, [full_record("2"), full_record("1")])
        output = self.base / "cli-output"
        completed = subprocess.run(
            [sys.executable, str(Path(exporter.__file__)),
             "--data-dir", str(self.data), "--output-dir", str(output),
             "--full-json", "--archive"],
            cwd="/", capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["records"], 2)
        self.assertEqual(set(summary["artifacts"]),
                         {exporter.MINIMAL_NAME, exporter.FULL_NAME,
                          exporter.ARCHIVE_NAME})
        minimal = json.loads((output / exporter.MINIMAL_NAME).read_text(encoding="utf-8"))
        records = corpus.load_articles(self.data)
        self.assertEqual(minimal,
                         {aid: corpus.minimal_record(record)
                          for aid, record in records.items()})


if __name__ == "__main__":
    unittest.main()
