from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
UPDATE = (ROOT / ".github/workflows/update-data.yml").read_text(encoding="utf-8")
BUILD = (ROOT / ".github/workflows/build-worker.yml").read_text(encoding="utf-8")
NATIVE_BUILD = (ROOT / "worker/scripts/build-cloudflare.sh").read_text(encoding="utf-8")
PARSER_TESTS = (ROOT / ".github/workflows/parse-tests.yml").read_text(encoding="utf-8")
PARSER_LIVE = (ROOT / ".github/workflows/parser-live-smoke.yml").read_text(encoding="utf-8")
README = (ROOT / "parse/README.md").read_text(encoding="utf-8")
REPOSITORY = "abusetelegram/xixi-haha"
DEFAULT_REF = "refs/heads/master"
SOURCE_SHA = "a" * 40
DATA_SHA = "b" * 40


def update_context_is_trusted(event, ref, mode):
    return ref == DEFAULT_REF and (
        event == "schedule" or (event == "workflow_dispatch" and mode in {
            "incremental", "full", "export-only",
        })
    )


def acquisition_limits(event, mode, max_pages="50", max_additions="200"):
    if event == "schedule":
        raw = ("50", "200")
    elif mode == "incremental":
        raw = (max_pages, max_additions)
    elif mode == "full":
        raw = ("2000", "5000")
    else:
        raise ValueError("not an acquisition mode")
    values = []
    for name, value, upper in zip(
            ("max_pages", "max_additions"), raw, (2000, 5000)):
        if not value.isascii() or not value.isdecimal():
            raise ValueError(f"{name} must be a positive decimal integer")
        parsed = int(value)
        if not 1 <= parsed <= upper:
            raise ValueError(f"{name} out of range")
        values.append(parsed)
    return tuple(values)


def build_context_is_trusted(event, ref, workflow_ref, source_sha="", data_sha=""):
    if ref != DEFAULT_REF:
        return False
    direct = (
        event == "push"
        and workflow_ref == f"{REPOSITORY}/.github/workflows/build-worker.yml@{ref}"
        and source_sha == "" and data_sha == ""
    )
    reusable = (
        event in {"schedule", "workflow_dispatch"}
        and workflow_ref == f"{REPOSITORY}/.github/workflows/update-data.yml@{ref}"
        and source_sha == SOURCE_SHA
        and bool(data_sha)
    )
    return direct or reusable


def live_smoke_path_matches(path):
    if path in {"parse/corpus.py", "parse/update.py", "parse/tests/live_smoke.py"}:
        return True
    prefix = "parse/tests/fixtures/"
    return path.startswith(prefix) and not path[len(prefix):].startswith("worker-")


class WorkflowTrustTests(unittest.TestCase):
    """Regress the checked-in guards; repository policy must constrain malicious authors."""

    def test_update_event_ref_matrix(self):
        cases = {
            "default schedule": ("schedule", DEFAULT_REF, "incremental", True),
            "default manual": ("workflow_dispatch", DEFAULT_REF, "full", True),
            "feature manual": ("workflow_dispatch", "refs/heads/feature/untrusted", "full", False),
            "fork pull request": ("pull_request", "refs/pull/16/merge", "incremental", False),
        }
        for name, (event, ref, mode, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(expected, update_context_is_trusted(event, ref, mode))

    def test_build_event_ref_source_matrix(self):
        update_ref = f"{REPOSITORY}/.github/workflows/update-data.yml@{DEFAULT_REF}"
        build_ref = f"{REPOSITORY}/.github/workflows/build-worker.yml@{DEFAULT_REF}"
        cases = {
            "default push": ("push", DEFAULT_REF, build_ref, "", "", True),
            "reusable schedule": ("schedule", DEFAULT_REF, update_ref, SOURCE_SHA, DATA_SHA, True),
            "reusable manual": ("workflow_dispatch", DEFAULT_REF, update_ref, SOURCE_SHA, DATA_SHA, True),
            "feature manual": ("workflow_dispatch", "refs/heads/feature/untrusted", update_ref, SOURCE_SHA, DATA_SHA, False),
            "wrong caller": ("workflow_dispatch", DEFAULT_REF, build_ref, SOURCE_SHA, DATA_SHA, False),
            "wrong source": ("workflow_dispatch", DEFAULT_REF, update_ref, "c" * 40, DATA_SHA, False),
            "fork pull request": ("pull_request", "refs/pull/16/merge", update_ref, SOURCE_SHA, DATA_SHA, False),
        }
        for name, (event, ref, workflow_ref, source_sha, data_sha, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(expected, build_context_is_trusted(
                    event, ref, workflow_ref, source_sha, data_sha))

    def test_manual_incremental_limit_matrix(self):
        self.assertEqual(acquisition_limits("schedule", "incremental", "999", "999"),
                         (50, 200))
        self.assertEqual(acquisition_limits("workflow_dispatch", "incremental"),
                         (50, 200))
        self.assertEqual(
            acquisition_limits("workflow_dispatch", "incremental", "1200", "3456"),
            (1200, 3456),
        )
        self.assertEqual(
            acquisition_limits("workflow_dispatch", "full", "1", "1"),
            (2000, 5000),
        )
        for pages, additions in (
                ("oops", "200"), ("1.5", "200"), ("", "200"),
                ("0", "200"), ("-1", "200"), ("2001", "200"),
                ("50", "0"), ("50", "-1"), ("50", "5001")):
            with self.subTest(pages=pages, additions=additions), self.assertRaises(ValueError):
                acquisition_limits(
                    "workflow_dispatch", "incremental", pages, additions)

    def test_manual_limits_do_not_weaken_ref_guard(self):
        for ref in ("refs/heads/feature/untrusted", "refs/pull/20/merge"):
            with self.subTest(ref=ref):
                self.assertFalse(update_context_is_trusted(
                    "workflow_dispatch", ref, "incremental"))

    def test_workflows_encode_trust_and_credential_contract(self):
        default_ref_guard = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        self.assertGreaterEqual(UPDATE.count(default_ref_guard), 4)
        self.assertIn(default_ref_guard, BUILD)
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", BUILD)
        self.assertIn("inputs.source_sha == github.sha && inputs.data_sha != ''", BUILD)
        self.assertIn(".github/workflows/update-data.yml@{1}", BUILD)
        self.assertIn("source_sha: ${{ github.sha }}", UPDATE)
        self.assertNotIn("secrets: inherit", UPDATE + BUILD)
        self.assertNotIn("secrets:", BUILD)
        self.assertNotIn("vars.", UPDATE + BUILD)
        self.assertNotIn("  deploy:", BUILD)
        self.assertNotIn("wrangler.js deploy", BUILD)
        self.assertNotIn("secrets.", BUILD)
        self.assertIn("cancel-in-progress: false", BUILD)
        self.assertNotIn("BOT_TOKEN", BUILD)
        self.assertNotIn("TELEGRAM_WEBHOOK_SECRET", BUILD)
        self.assertIn("uses: ./.github/workflows/build-worker.yml", UPDATE)
        self.assertNotIn("deploy-worker.yml", UPDATE + BUILD)
        self.assertIn("package:deployment", BUILD)

    def test_native_build_is_production_configured_and_never_publishes(self):
        self.assertIn("SOURCE_SHA=$(git rev-parse HEAD)", NATIVE_BUILD)
        self.assertIn("refs/heads/data", NATIVE_BUILD)
        self.assertIn("--source-sha \"$SOURCE_SHA\" --data-sha \"$DATA_SHA\"", NATIVE_BUILD)
        self.assertIn("validate-deployment.py", NATIVE_BUILD)
        self.assertIn("npm run package:deployment", NATIVE_BUILD)
        self.assertNotIn("wrangler deploy", NATIVE_BUILD)
        self.assertNotIn("secrets.", NATIVE_BUILD)

    def test_live_smoke_trigger_isolated_from_worker_and_workflow_changes(self):
        current_stack_paths = (
            ".github/workflows/worker-core-tests.yml",
            ".github/workflows/parser-live-smoke.yml",
            "worker/src/asset-repository.ts",
            "docs/worker-data-format.md",
            "parse/export_worker.py",
            "parse/tests/test_export_worker.py",
            "parse/tests/fixtures/worker-index-v1.bin",
            "parse/tests/fixtures/worker-selectable-v1.json",
        )
        for path in current_stack_paths:
            with self.subTest(path=path):
                self.assertFalse(live_smoke_path_matches(path))
        for path in (
                "parse/corpus.py", "parse/update.py", "parse/tests/live_smoke.py",
                "parse/tests/fixtures/upstream-page.html"):
            with self.subTest(path=path):
                self.assertTrue(live_smoke_path_matches(path))

    def test_live_smoke_workflow_contract(self):
        self.assertIn("workflow_dispatch:", PARSER_LIVE)
        self.assertNotIn(".github/workflows", PARSER_LIVE)
        self.assertIn("- 'parse/corpus.py'", PARSER_LIVE)
        self.assertIn("- 'parse/update.py'", PARSER_LIVE)
        self.assertIn("- '!parse/tests/fixtures/worker-*'", PARSER_LIVE)
        self.assertIn("python parse/tests/live_smoke.py", PARSER_LIVE)
        self.assertNotIn("live_smoke.py", PARSER_TESTS)
        self.assertIn(".github/workflows/*.yml", PARSER_TESTS)
        self.assertIn("python -m unittest discover -s parse/tests -v", PARSER_TESTS)

    def test_full_scan_uses_cautious_pacing(self):
        self.assertIn(
            "args+=(--delay 2.1 --full-scan --max-pages 2000 --max-additions 5000)",
            UPDATE,
        )
        self.assertIn(
            'args+=(--delay 1 --max-pages "$MAX_PAGES" --max-additions "$MAX_ADDITIONS")',
            UPDATE,
        )
        self.assertNotIn("--full-scan --max-pages \"$MAX_PAGES\"", UPDATE)
        self.assertIn('values = {"max_pages": "50", "max_additions": "200"}', UPDATE)
        self.assertIn('upper_bounds = {"max_pages": 2000, "max_additions": 5000}', UPDATE)
        self.assertIn('INPUT_MAX_PAGES: ${{ inputs.max_pages }}', UPDATE)
        self.assertIn('MAX_PAGES: ${{ needs.validate_acquisition.outputs.max_pages }}', UPDATE)
        self.assertIn(
            '--data-dir "$DATA" --full-scan \\\n'
            '  --max-pages 2000 --max-additions 5000 \\\n'
            '  --delay 2.1 --timeout 30 --retries 3',
            README,
        )


if __name__ == "__main__":
    unittest.main()
