import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import export_worker

SOURCE_SHA = "2" * 40
DATA_SHA = "1" * 40
FIXTURES = Path(__file__).parent / "fixtures"


def ordinary(aid, text=None, **changes):
    record = {
        "id": str(aid), "title": "标题 {}".format(aid),
        "date": "2026-01-02 03:04:05", "author": "作者", "editor": "编辑",
        "article": '<div class="d2txt_con"><p>原文 {}</p></div>'.format(aid),
        "text": ["段落 {}".format(aid)] if text is None else text,
    }
    record.update(changes)
    return record


def media_record():
    url = "https://cpc.people.com.cn/NMediaFile/2023/1216/MAIN170271463331969PTAF569C.jpg"
    return ordinary(
        40140589, text=[],
        title="时习之｜健全城市社区治理体系 习近平牵挂“最后一公里”",
        date="2023-12-17 09:49:04", author="人民网-中国共产党新闻网", editor="王潇潇",
        article='<div class="d2txt_con"><img src="{}" alt=""></div>'.format(url),
        content_type="image", media=[{"type": "image", "url": url, "alt": ""}])


class WorkerExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.output = self.root / "output"

    def create(self, records):
        corpus.create_articles(self.data, records, strict_content=False)

    def export(self, shards=4):
        return export_worker.export_worker(
            self.data, self.output, SOURCE_SHA, DATA_SHA, shards, verify_refs=False)

    def snapshot_source(self):
        return {str(path.relative_to(self.data)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.data.rglob("*") if path.is_file()}

    def test_deterministic_projection_binary_contract_and_no_source_write(self):
        selection = json.loads(
            (FIXTURES / "worker-selectable-v1.json").read_text(encoding="utf-8"))
        boundary_text = [case["text"] for case in selection["cases"]]
        self.create([ordinary(1, boundary_text), ordinary(2, []), ordinary(5, ["三"])])
        before = self.snapshot_source()
        first = self.export()
        first_bytes = {str(path.relative_to(self.output)): path.read_bytes()
                       for path in self.output.rglob("*") if path.is_file()}
        second = self.export()
        second_bytes = {str(path.relative_to(self.output)): path.read_bytes()
                        for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(before, self.snapshot_source())
        self.assertNotIn(b"<div", b"".join(first_bytes.values()))

        raw = first_bytes["_data/{}/paragraph-index.bin".format(DATA_SHA)]
        self.assertEqual(raw, (FIXTURES / "worker-index-v1.bin").read_bytes())
        self.assertEqual(raw[:16], struct.pack(">4sIII", b"XHPI", 1, 3, 4))
        self.assertEqual(raw[16:], struct.pack(">IIIIII", 1, 3, 2, 3, 5, 4))
        decoded = export_worker.decode_paragraph_index(raw)
        self.assertEqual(decoded["entries"], [(1, 3), (2, 3), (5, 4)])
        self.assertEqual(first["counts"], {
            "articles": 3, "selectableArticles": 2,
            "sourceParagraphs": 8, "selectableParagraphs": 4})

    def test_projection_preserves_values_and_image_media(self):
        record = ordinary(7, ["  空格保持  ", ""] , title=" A & B ", author="")
        image = media_record()
        self.create([record, image])
        manifest = self.export()
        rows = []
        for shard in manifest["sharding"]["shards"]:
            rows.extend(json.loads((self.output / shard["path"]).read_text(encoding="utf-8")))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["7"]["text"], ["  空格保持  ", ""])
        self.assertEqual(manifest["counts"], {
            "articles": 2, "selectableArticles": 1,
            "sourceParagraphs": 2, "selectableParagraphs": 1})
        self.assertEqual(by_id["7"]["title"], " A & B ")
        self.assertEqual(by_id["7"]["author"], "")
        self.assertNotIn("article", by_id["7"])
        self.assertEqual(by_id["40140589"]["content_type"], "image")
        self.assertEqual(by_id["40140589"]["media"], image["media"])

    def test_thirty_empty_records_are_lookup_records_but_have_zero_index_ranges(self):
        records = [ordinary(index, []) for index in range(1, 31)]
        records.append(ordinary(31, ["only quote"]))
        self.create(records)
        manifest = self.export()
        raw = (self.output / manifest["paragraphIndex"]["path"]).read_bytes()
        decoded = export_worker.decode_paragraph_index(raw)
        self.assertEqual(manifest["counts"], {
            "articles": 31, "selectableArticles": 1,
            "sourceParagraphs": 1, "selectableParagraphs": 1})
        self.assertEqual([offset for _, offset in decoded["entries"][:30]], [0] * 30)
        self.assertEqual(decoded["entries"][-1], (31, 1))
        self.assertEqual(sum(shard["recordCount"] for shard in
                             manifest["sharding"]["shards"]), 31)

    def test_assignment_is_id_modulo_power_of_two_and_manifest_driven(self):
        self.create([ordinary(1), ordinary(9), ordinary(10)])
        manifest = self.export(shards=8)
        self.assertEqual(manifest["sharding"]["shardCount"], 8)
        mapping = {shard["shard"]: shard["recordCount"]
                   for shard in manifest["sharding"]["shards"]}
        self.assertEqual(mapping[1], 2)
        self.assertEqual(mapping[2], 1)
        self.assertEqual(len(mapping), 8)

    def test_selectability_golden_and_rank_mapping_preserve_original_indexes(self):
        fixture = json.loads(
            (FIXTURES / "worker-selectable-v1.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(case=case["label"]):
                self.assertEqual(export_worker.is_selectable_paragraph(case["text"]),
                                 case["selectable"])
        example = fixture["rankMappingExample"]
        selected = [(index, value) for index, value in enumerate(example["text"])
                    if export_worker.is_selectable_paragraph(value)]
        expected = [(item["originalTextIndex"], item["exactPayload"])
                    for item in example["selectedRanks"]]
        self.assertEqual(selected, expected)

    def test_invalid_shard_counts_and_shas_fail(self):
        self.create([ordinary(1)])
        for count in (0, 3, -2, True, 1 << 15, 1 << 31):
            with self.subTest(count=count), self.assertRaises(export_worker.ExportError):
                self.export(count)
        self.assertFalse(self.output.exists())
        export_worker._validate_shard_count(1 << 14)
        with self.assertRaises(export_worker.ExportError):
            export_worker.export_worker(self.data, self.output, "ABC", DATA_SHA,
                                         verify_refs=False)

    def test_invalid_canonical_dataset_fails_without_output(self):
        articles = self.data / "articles"
        articles.mkdir(parents=True)
        (articles / "1.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(export_worker.ExportError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_static_asset_size_and_file_count_gates_fail_before_publication(self):
        self.create([ordinary(1)])
        with patch.object(export_worker, "MAX_ASSET_BYTES", 1):
            with self.assertRaisesRegex(export_worker.ExportError, "exceeds"):
                self.export()
        self.assertFalse(self.output.exists())
        with patch.object(export_worker, "MAX_ASSET_FILES", 6):
            with self.assertRaisesRegex(export_worker.ExportError, "files"):
                self.export()
        self.assertFalse(self.output.exists())

    def test_symlink_dataset_and_output_are_rejected(self):
        real = self.root / "real"
        corpus.create_articles(real, [ordinary(1)])
        self.data.mkdir()
        os.symlink(real / "articles", self.data / "articles")
        with self.assertRaisesRegex(export_worker.ExportError, "real directory"):
            self.export()
        self.data.unlink() if self.data.is_symlink() else None

        # Use a valid second data directory for the output-link check.
        self.data = self.root / "data2"
        self.create([ordinary(1)])
        target = self.root / "target"
        target.mkdir()
        os.symlink(target, self.output)
        with self.assertRaisesRegex(export_worker.ExportError, "symlink"):
            self.export()

    def test_failed_replacement_restores_previous_output(self):
        self.create([ordinary(1)])
        self.output.mkdir()
        marker = self.output / "old.txt"
        marker.write_text("old", encoding="utf-8")
        real_replace = os.replace

        def fail_stage(source, destination):
            if ".stage-" in str(source) and Path(destination).name == self.output.name:
                raise OSError("simulated output failure")
            return real_replace(source, destination)

        with patch.object(export_worker.os, "replace", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.export()
        self.assertEqual(marker.read_text(encoding="utf-8"), "old")
        self.assertFalse((self.root / ".output.old").exists())

    def test_output_inside_data_is_rejected(self):
        self.create([ordinary(1)])
        with self.assertRaisesRegex(export_worker.ExportError, "outside"):
            export_worker.export_worker(
                self.data, self.data / "generated", SOURCE_SHA, DATA_SHA,
                verify_refs=False)

    def test_checkout_verification_fails_closed(self):
        self.create([ordinary(1)])
        with self.assertRaisesRegex(export_worker.ExportError, "checkout HEAD"):
            export_worker.export_worker(self.data, self.output, SOURCE_SHA, DATA_SHA)


class IndexValidationTests(unittest.TestCase):
    def test_decoder_rejects_truncation_bad_header_and_nonmonotonic_entries(self):
        cases = [
            b"XHPI",
            struct.pack(">4sIII", b"NOPE", 1, 0, 0),
            struct.pack(">4sIII", b"XHPI", 2, 0, 0),
            struct.pack(">4sIII", b"XHPI", 1, 1, 2) + struct.pack(">II", 1, 1),
            (struct.pack(">4sIII", b"XHPI", 1, 2, 1)
             + struct.pack(">IIII", 2, 1, 1, 1)),
        ]
        for content in cases:
            with self.subTest(content=content), self.assertRaises(export_worker.ExportError):
                export_worker.decode_paragraph_index(content)

    def test_encoder_rejects_unsorted_duplicate_and_uint32_overflow(self):
        with self.assertRaises(export_worker.ExportError):
            export_worker.encode_paragraph_index([
                {"id": "2", "text": []}, {"id": "1", "text": []}])
        with self.assertRaises(export_worker.ExportError):
            export_worker.encode_paragraph_index([{"id": str(1 << 32), "text": []}])


if __name__ == "__main__":
    unittest.main()
