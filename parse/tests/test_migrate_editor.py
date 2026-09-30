import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import migrate_editor


def record(aid, editor):
    return {
        "id": str(aid),
        "title": "标题",
        "date": "2026-09-24 08:28:01",
        "author": "来源",
        "editor": editor,
        "article": '<div class="d2txt_con"><p>正文 &amp; evidence</p></div>',
        "text": ["正文 & evidence"],
    }


class EditorMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        articles = self.data / "articles"
        articles.mkdir()
        self.records = {
            "1": record("1", "(责编：张三   )"),
            "2": record("2", "不明"),
            "3": record("3", "(责编：实习生)"),
        }
        for aid, value in self.records.items():
            (articles / (aid + ".json")).write_bytes(corpus.serialize_record(value))

    def test_dry_run_is_read_only_and_apply_changes_only_editor(self):
        before = {aid: json.loads((self.data / "articles" / (aid + ".json")).read_text())
                  for aid in self.records}
        _, replacements, summary = migrate_editor.migration_plan(self.data)
        self.assertEqual(summary["records"], 3)
        self.assertEqual(summary["changed"], 2)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["non_editor_changes"], 0)
        self.assertEqual(summary["role_only_review_ids"], ["3"])
        self.assertEqual(summary["unknown_editor_ids"], ["2"])
        self.assertEqual(set(replacements), {"1", "3"})
        self.assertEqual(before, {
            aid: json.loads((self.data / "articles" / (aid + ".json")).read_text())
            for aid in self.records})

        migrate_editor.apply_plan(self.data, replacements)
        after = corpus.load_articles(self.data)
        self.assertEqual(after["1"]["editor"], "张三")
        self.assertEqual(after["2"]["editor"], "不明")
        self.assertEqual(after["3"]["editor"], "实习生")
        for aid in self.records:
            self.assertEqual({key: value for key, value in before[aid].items() if key != "editor"},
                             {key: value for key, value in after[aid].items() if key != "editor"})
        _, second_replacements, second = migrate_editor.migration_plan(self.data)
        self.assertEqual(second_replacements, {})
        self.assertEqual(second["changed"], 0)
        self.assertEqual(second["normalized_sha256"], summary["normalized_sha256"])

    def test_cli_expectations_detect_drift_before_apply(self):
        code = migrate_editor.main([
            "--data-dir", str(self.data), "--apply",
            "--expect-records", "999", "--expect-changed", "2",
        ])
        self.assertEqual(code, 1)
        self.assertEqual(corpus.load_articles(self.data)["1"]["editor"], "(责编：张三   )")


if __name__ == "__main__":
    unittest.main()
