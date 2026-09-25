import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


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
        historical = record(1)
        historical["title"] = ""
        historical["text"] = []
        (self.seed / "articles" / "1.json").write_bytes(corpus.serialize_record(historical))
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

    def test_no_change_rejects_advanced_remote(self):
        checkout = self.clone("stale-no-change")
        racer = self.clone("no-change-winner")
        (racer / "notice.txt").write_text("advance\n", encoding="utf-8")
        run("git", "-C", str(racer), "add", "notice.txt")
        run("git", "-C", str(racer), "commit", "-m", "advance remote")
        run("git", "-C", str(racer), "push", "origin", "data")

        with self.assertRaisesRegex(data_pipeline.PipelineError, "Remote data branch advanced"):
            data_pipeline.publish_changes(
                checkout, self.report(self.root, known=1, added=0, records=1),
                self.start_sha, SOURCE_SHA, "https://example.invalid/run/stale-no-change")

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

    def test_dry_run_rejects_advanced_remote(self):
        checkout = self.clone("stale-dry-run")
        (checkout / "articles" / "2.json").write_bytes(corpus.serialize_record(record(2)))
        racer = self.clone("dry-run-winner")
        (racer / "notice.txt").write_text("advance\n", encoding="utf-8")
        run("git", "-C", str(racer), "add", "notice.txt")
        run("git", "-C", str(racer), "commit", "-m", "advance remote")
        run("git", "-C", str(racer), "push", "origin", "data")

        with self.assertRaisesRegex(data_pipeline.PipelineError, "Remote data branch advanced"):
            data_pipeline.publish_changes(
                checkout, self.report(self.root, known=1, added=1, records=2),
                self.start_sha, SOURCE_SHA, "https://example.invalid/run/stale-dry", dry_run=True)

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
        self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD^"))
        self.assertEqual(
            corpus.serialize_record(record(2)),
            subprocess.run(
                ["git", "-C", str(checkout), "show", "HEAD:articles/2.json"],
                check=True, stdout=subprocess.PIPE,
            ).stdout,
        )
        self.assertEqual(
            result["data_sha"],
            run("git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/data"),
        )

    def test_new_article_requires_strict_content_but_historical_empty_is_allowed(self):
        checkout = self.clone("strict-new")
        invalid = record(2)
        invalid["text"] = []
        (checkout / "articles" / "2.json").write_bytes(corpus.serialize_record(invalid))
        with self.assertRaisesRegex(corpus.CorpusError, "must have nonempty text"):
            data_pipeline.publish_changes(
                checkout, self.report(self.root, known=1, added=1, records=2),
                self.start_sha, SOURCE_SHA, "https://example.invalid/run/strict")
        self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD"))
        self.assertEqual("", run("git", "-C", str(checkout), "diff", "--cached", "--name-only"))

    def test_edit_between_validation_and_staging_is_rejected_and_unstaged(self):
        checkout = self.clone("stage-race")
        article = checkout / "articles" / "2.json"
        article.write_bytes(corpus.serialize_record(record(2)))
        tampered = record(2)
        tampered["title"] = "changed after validation"
        original_git = data_pipeline._git
        changed = []

        def mutate_before_add(directory, *args, **kwargs):
            if args and args[0] == "add" and not changed:
                article.write_bytes(corpus.serialize_record(tampered))
                changed.append(True)
            return original_git(directory, *args, **kwargs)

        with mock.patch.object(data_pipeline, "_git", side_effect=mutate_before_add):
            with self.assertRaisesRegex(data_pipeline.PipelineError, "not exactly the validated"):
                data_pipeline.publish_changes(
                    checkout, self.report(self.root, known=1, added=1, records=2),
                    self.start_sha, SOURCE_SHA, "https://example.invalid/run/stage-race")
        self.assertEqual([True], changed)
        self.assertEqual(self.start_sha, run("git", "-C", str(checkout), "rev-parse", "HEAD"))
        self.assertEqual("", run("git", "-C", str(checkout), "diff", "--cached", "--name-only"))

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

    def test_normal_push_rejects_race_after_preflight(self):
        publisher = self.clone("late-race-publisher")
        racer = self.clone("late-race-winner")
        (publisher / "articles" / "2.json").write_bytes(corpus.serialize_record(record(2)))
        original_remote_data_sha = data_pipeline._remote_data_sha
        checks = []

        def race_after_snapshot(directory):
            snapshot = original_remote_data_sha(directory)
            checks.append(snapshot)
            if len(checks) == 2:
                (racer / "notice.txt").write_text("late advance\n", encoding="utf-8")
                run("git", "-C", str(racer), "add", "notice.txt")
                run("git", "-C", str(racer), "commit", "-m", "late advance remote")
                run("git", "-C", str(racer), "push", "origin", "data")
            return snapshot

        with mock.patch.object(data_pipeline, "_remote_data_sha", side_effect=race_after_snapshot):
            with self.assertRaises(data_pipeline.PipelineError):
                data_pipeline.publish_changes(
                    publisher, self.report(self.root, known=1, added=1, records=2),
                    self.start_sha, SOURCE_SHA, "https://example.invalid/run/late-race")
        self.assertEqual(2, len(checks))
        self.assertEqual(
            run("git", "-C", str(racer), "rev-parse", "HEAD"),
            run("git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/data"),
        )

    def test_export_reads_immutable_commit_snapshot(self):
        code = self.root / "snapshot-code"
        run("git", "init", "-b", "master", str(code))
        run("git", "-C", str(code), "config", "user.name", "Test")
        run("git", "-C", str(code), "config", "user.email", "test@example.invalid")
        (code / "tracked").write_text("code\n", encoding="utf-8")
        run("git", "-C", str(code), "add", "tracked")
        run("git", "-C", str(code), "commit", "-m", "code")
        code_sha = run("git", "-C", str(code), "rev-parse", "HEAD")
        data = self.clone("snapshot-export-data")
        output = self.root / "snapshot-exports"
        observed = []

        def mutate_checkout(snapshot, _output, **kwargs):
            changed = record(1)
            changed["title"] = "mutable checkout changed"
            (data / "articles" / "1.json").write_bytes(corpus.serialize_record(changed))
            observed.append((snapshot, (snapshot / "articles" / "1.json").read_bytes(), kwargs))
            return {"records": 1, "artifacts": {}}

        result = data_pipeline.export_exact(
            code, data, output, code_sha, self.start_sha,
            export_function=mutate_checkout,
        )
        self.assertEqual(1, result["records"])
        self.assertEqual(1, len(observed))
        snapshot, snapshot_bytes, kwargs = observed[0]
        self.assertNotEqual(data, snapshot)
        self.assertFalse(snapshot.exists())
        historical = record(1)
        historical["title"] = ""
        historical["text"] = []
        self.assertEqual(corpus.serialize_record(historical), snapshot_bytes)
        self.assertEqual(self.start_sha, kwargs["data_sha"])
        self.assertNotEqual(snapshot_bytes, (data / "articles" / "1.json").read_bytes())

    def test_export_rejects_and_preserves_dangling_output_symlink(self):
        code = self.root / "symlink-code"
        run("git", "init", "-b", "master", str(code))
        run("git", "-C", str(code), "config", "user.name", "Test")
        run("git", "-C", str(code), "config", "user.email", "test@example.invalid")
        (code / "tracked").write_text("code\n", encoding="utf-8")
        run("git", "-C", str(code), "add", "tracked")
        run("git", "-C", str(code), "commit", "-m", "code")
        code_sha = run("git", "-C", str(code), "rev-parse", "HEAD")
        data = self.clone("symlink-export-data")
        target = self.root / "missing-export-target"
        output = self.root / "exports-link"
        output.symlink_to(target, target_is_directory=True)
        called = []

        def unexpected_export(*_args, **_kwargs):
            called.append(True)

        with self.assertRaisesRegex(data_pipeline.PipelineError, "must not already exist or be a symlink"):
            data_pipeline.export_exact(
                code, data, output, code_sha, self.start_sha,
                export_function=unexpected_export,
            )
        self.assertTrue(output.is_symlink())
        self.assertEqual(target, output.readlink())
        self.assertFalse(target.exists())
        self.assertEqual([], called)

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
