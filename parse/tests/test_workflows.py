from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
UPDATE = (ROOT / ".github/workflows/update-data.yml").read_text(encoding="utf-8")
DOCKER = (ROOT / ".github/workflows/docker-telegram.yml").read_text(encoding="utf-8")
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


def docker_context_is_trusted(event, ref, workflow_ref, source_sha="", data_sha=""):
    if ref != DEFAULT_REF:
        return False
    direct = (
        event == "push"
        and workflow_ref == f"{REPOSITORY}/.github/workflows/docker-telegram.yml@{ref}"
        and source_sha == ""
    )
    reusable = (
        event in {"schedule", "workflow_dispatch"}
        and workflow_ref == f"{REPOSITORY}/.github/workflows/update-data.yml@{ref}"
        and source_sha == SOURCE_SHA
        and bool(data_sha)
    )
    return direct or reusable


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

    def test_docker_event_ref_source_matrix(self):
        update_ref = f"{REPOSITORY}/.github/workflows/update-data.yml@{DEFAULT_REF}"
        docker_ref = f"{REPOSITORY}/.github/workflows/docker-telegram.yml@{DEFAULT_REF}"
        cases = {
            "default push": ("push", DEFAULT_REF, docker_ref, "", "", True),
            "reusable schedule": ("schedule", DEFAULT_REF, update_ref, SOURCE_SHA, DATA_SHA, True),
            "reusable manual": ("workflow_dispatch", DEFAULT_REF, update_ref, SOURCE_SHA, DATA_SHA, True),
            "feature reusable": ("workflow_dispatch", "refs/heads/feature/untrusted", update_ref, SOURCE_SHA, DATA_SHA, False),
            "wrong reusable source": ("workflow_dispatch", DEFAULT_REF, update_ref, "c" * 40, DATA_SHA, False),
            "fork pull request": ("pull_request", "refs/pull/16/merge", update_ref, SOURCE_SHA, DATA_SHA, False),
        }
        for name, (event, ref, workflow_ref, source_sha, data_sha, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    docker_context_is_trusted(event, ref, workflow_ref, source_sha, data_sha),
                )

    def test_workflows_encode_trust_contract(self):
        default_ref_guard = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        self.assertGreaterEqual(UPDATE.count(default_ref_guard), 4)
        self.assertGreaterEqual(DOCKER.count(default_ref_guard), 2)
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", DOCKER)
        self.assertIn("inputs.source_sha == github.sha && inputs.data_sha != ''", DOCKER)
        self.assertIn(".github/workflows/update-data.yml@{1}", DOCKER)
        self.assertIn("source_sha: ${{ github.sha }}", UPDATE)
        self.assertNotIn("secrets: inherit", UPDATE)
        self.assertIn("DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}", UPDATE)
        self.assertIn("DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}", UPDATE)

    def test_full_scan_uses_cautious_pacing(self):
        self.assertIn(
            "args+=(--delay 2.1 --full-scan --max-pages 2000 --max-additions 5000)",
            UPDATE,
        )
        self.assertIn("args+=(--delay 1 --max-pages 50 --max-additions 200)", UPDATE)


if __name__ == "__main__":
    unittest.main()
