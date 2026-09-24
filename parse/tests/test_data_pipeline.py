import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "data_pipeline", ROOT / "scripts" / "data_pipeline.py")
data_pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data_pipeline)

import corpus


SOURCE_SHA = "a" * 40


def run(*args, cwd=None):
    return subprocess.run(
        args, cwd=cwd, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


def record(aid):
    return {
        "id": str(aid),
        "title": "title " + str(aid),
        "date": "2026-01-01",
        "author": "author",
        "editor": "editor",
        "article": "<div>body</div>",
        "text": ["body"],
    }


class DataPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        run("git", "init", "--bare", str(self.remote))
        run("git", "init", "-b", "data", str(self.seed))
        run("git", "-C", str(self.seed), "config", "user.name", "Test")
        run("git", "-C", str(self.seed), "config", "user.email", "test@example.invalid")
        (self.seed / "articles").mkdir()
        (self.seed / "README.md").write_text("data\n", encoding="utf-8")
        (self.seed / "articles" / "1.json").write_bytes(corpus.serialize_record(record(1)))
        run("git", "-C", str(self.seed), "add", ".")
        run("git", "-C", str(self.seed), "commit", "-m", "initial")
        run("git", "-C", str(self.seed), "remote", "add", "origin", str(self.remote))
        run("git", "-C", str(self.seed), "push", "-u", "origin", "data")
        run("git", "--git-dir", str(self.remote), "symbolic-ref", "HEAD", "refs/heads/data")
        self.start_sha = run("git", "-C", str(self.seed), "rev-parse", "HEAD")

    def tearDown(self):
        self.temporary.cleanup()

    def clone(self, name):
        path = self.root / name
        run("git", "clone", str(self.remote), str(path))
        run("git", "-C", str(path), "config", "user.name", "Test")
        run("git", "-C", str(path), "config", "user.email", "test@example.invalid")
        return path

    def report(self, directory, *, known, added, records):
        path = directory / "report.json"
        path.write_text(json.dumps({
            "status": "success", "complete": True,
            "known": known, "added": added, "records": records,
        }), encoding="utf-8")
        return path

    def test_no_change_creates_no_commit(self):
        checkout = self.clone("no-change")
        result = data_pipeline.publish_changes(
            checkout, self.report(self.root, known=1, added=0, records=1),
            self.start_sha, SOURCE_SHA, "https://example.invalid/run/1")
        self.assertEqual("no-change", result["status"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD"))

    def test_dry_run_validates_addition_without_commit_or_push(self):
        checkout = self.clone("dry-run")
        (checkout / "articles" / "2.json").write_bytes(corpus.serialize_record(record(2)))
        result = data_pipeline.publish_changes(
            checkout, self.report(self.root, known=1, added=1, records=2),
            self.start_sha, SOURCE_SHA, "https://example.invalid/run/dry", dry_run=True)
        self.assertEqual("dry-run", result["status"])
        self.assertTrue(result["changed"])
        self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD"))
        self.assertEqual(
            self.start_sha,
            run("git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/data"),
        )

    def test_nonzero_acquisition_commits_only_new_article_and_pushes(self):
        checkout = self.clone("publisher")
        (checkout / "articles" / "2.json").write_bytes(corpus.serialize_record(record(2)))
        result = data_pipeline.publish_changes(
            checkout, self.report(self.root, known=1, added=1, records=2),
            self.start_sha, SOURCE_SHA, "https://example.invalid/run/2")
        self.assertEqual("published", result["status"])
        self.assertEqual(1, result["added"])
        self.assertEqual(
            "articles/2.json",
            run("git", "-C", str(checkout), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"),
        )
        self.assertEqual(
            result["data_sha"],
            run("git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/data"),
        )

    def test_tracked_mutation_and_deletion_are_rejected(self):
        for index, mutate in enumerate((
                lambda checkout: (checkout / "articles" / "1.json").write_text("{}\n", encoding="utf-8"),
                lambda checkout: (checkout / "articles" / "1.json").unlink())):
            with self.subTest(index=index):
                checkout = self.clone("bad-{}".format(index))
                mutate(checkout)
                with self.assertRaisesRegex(data_pipeline.PipelineError, "only add untracked"):
                    data_pipeline.publish_changes(
                        checkout, self.report(self.root, known=1, added=0, records=1),
                        self.start_sha, SOURCE_SHA, "https://example.invalid/run/3")
                self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD"))

    def test_remote_advance_rejects_normal_push(self):
        publisher = self.clone("race-publisher")
        racer = self.clone("race-winner")
        (racer / "notice.txt").write_text("advance\n", encoding="utf-8")
        run("git", "-C", str(racer), "add", "notice.txt")
        run("git", "-C", str(racer), "commit", "-m", "advance remote")
        run("git", "-C", str(racer), "push", "origin", "data")
        winner_sha = run("git", "-C", str(racer), "rev-parse", "HEAD")

        (publisher / "articles" / "2.json").write_bytes(corpus.serialize_record(record(2)))
        with self.assertRaisesRegex(data_pipeline.PipelineError, "Remote data branch advanced"):
            data_pipeline.publish_changes(
                publisher, self.report(self.root, known=1, added=1, records=2),
                self.start_sha, SOURCE_SHA, "https://example.invalid/run/4")
        self.assertEqual(
            winner_sha,
            run("git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/data"),
        )

    def test_export_failure_removes_partial_output(self):
        code = self.root / "code"
        run("git", "init", "-b", "master", str(code))
        run("git", "-C", str(code), "config", "user.name", "Test")
        run("git", "-C", str(code), "config", "user.email", "test@example.invalid")
        (code / "tracked").write_text("code\n", encoding="utf-8")
        run("git", "-C", str(code), "add", "tracked")
        run("git", "-C", str(code), "commit", "-m", "code")
        code_sha = run("git", "-C", str(code), "rev-parse", "HEAD")
        data = self.clone("export-data")
        output = self.root / "exports"

        def failing_export(_data, output_dir, **_kwargs):
            output_dir.mkdir()
            (output_dir / "partial.json").write_text("partial", encoding="utf-8")
            raise RuntimeError("synthetic export failure")

        with self.assertRaisesRegex(data_pipeline.PipelineError, "synthetic export failure"):
            data_pipeline.export_exact(
                code, data, output, code_sha, self.start_sha,
                export_function=failing_export,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
