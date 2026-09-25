#!/usr/bin/env python3
"""Fail-closed helpers for canonical data publication and exact-SHA exports.

The network updater and deterministic exporter remain separate, authoritative
commands.  This module validates their handoff to Git: routine publication may
only add canonical article files, and exports must come from clean checkouts at
explicit commits.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parse"))

import corpus  # noqa: E402
import export as corpus_export  # noqa: E402


SHA_RE = re.compile(r"[0-9a-f]{40}")
ARTICLE_RE = re.compile(r"articles/([1-9][0-9]*)\.json")


class PipelineError(RuntimeError):
    """The update/export handoff is unsafe to publish."""


def _git(directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(directory), *args], check=check,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "git command failed"
        raise PipelineError(detail) from exc


def _exact_sha(value: str, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PipelineError("{} must be an exact lowercase 40-hex commit SHA".format(name))
    return value


def _status_entries(directory: Path):
    raw = _git(directory, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    fields = raw.split("\0")
    entries = []
    index = 0
    while index < len(fields) and fields[index]:
        item = fields[index]
        if len(item) < 4 or item[2] != " ":
            raise PipelineError("Cannot parse git status entry")
        status = item[:2]
        path = item[3:]
        index += 1
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise PipelineError("Cannot parse renamed git status entry")
            path = path + " -> " + fields[index]
            index += 1
        entries.append((status, path))
    return entries


def _read_update_report(path: Path) -> dict:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("Invalid updater report: {}".format(exc)) from exc
    if not isinstance(report, dict):
        raise PipelineError("Updater report must be a JSON object")
    if report.get("status") != "success" or report.get("complete") is not True:
        raise PipelineError("Updater did not report a complete successful scan")
    for field in ("known", "added", "records"):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PipelineError("Updater report has invalid {}".format(field))
    if report["known"] + report["added"] != report["records"]:
        raise PipelineError("Updater counts are inconsistent")
    return report


def _remote_data_sha(directory: Path) -> str:
    output = _git(
        directory, "ls-remote", "--exit-code", "origin", "refs/heads/data"
    ).stdout.strip().splitlines()
    if len(output) != 1:
        raise PipelineError("Remote data branch is missing or ambiguous")
    fields = output[0].split()
    if len(fields) != 2 or fields[1] != "refs/heads/data":
        raise PipelineError("Unexpected remote data branch response")
    return _exact_sha(fields[0], "remote data SHA")


def _require_remote_data_sha(directory: Path, expected_sha: str) -> None:
    remote_sha = _remote_data_sha(directory)
    if remote_sha != expected_sha:
        raise PipelineError(
            "Remote data branch advanced from {} to {}; refusing to publish".format(
                expected_sha, remote_sha))


def _validated_new_article(path: Path, expected_id: str) -> bytes:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        record = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("Cannot validate new article {}: {}".format(path, exc)) from exc
    validated = corpus.canonical_record(record, strict_content=True)
    if validated["id"] != expected_id:
        raise PipelineError("Filename/record ID mismatch for {}".format(path.name))
    if raw != corpus.serialize_record(validated, strict_content=True):
        raise PipelineError("New article {} is not deterministic LF pretty JSON".format(path.name))
    return raw


def _staged_bytes(directory: Path, path: str) -> bytes:
    return _git(directory, "show", ":" + path).stdout.encode("utf-8")


def _materialize_commit(directory: Path, sha: str, destination: Path) -> None:
    try:
        archive = subprocess.run(
            ["git", "-C", str(directory), "archive", "--format=tar", sha],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        destination.mkdir()
        subprocess.run(
            ["tar", "-xf", "-", "-C", str(destination)], input=archive,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise PipelineError(detail.strip() or "Cannot materialize exact data commit") from exc


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def publish_changes(data_dir: Path, report_path: Path, start_sha: str, source_sha: str,
                    run_url: str, dry_run: bool = False) -> dict:
    """Validate updater output, then commit and normally push additions only."""
    data = Path(data_dir).resolve()
    start_sha = _exact_sha(start_sha, "starting data SHA")
    source_sha = _exact_sha(source_sha, "source SHA")
    if _git(data, "rev-parse", "HEAD").stdout.strip() != start_sha:
        raise PipelineError("Data checkout moved after acquisition started")
    if _git(data, "branch", "--show-current").stdout.strip() != "data":
        raise PipelineError("Data checkout must be on the data branch")

    report = _read_update_report(report_path)
    entries = _status_entries(data)
    additions = []
    for status, path in entries:
        if status != "??" or ARTICLE_RE.fullmatch(path) is None:
            raise PipelineError(
                "Routine automation may only add untracked canonical articles; found {} {}".format(
                    status, path))
        additions.append(path)
    additions.sort(key=lambda path: int(ARTICLE_RE.fullmatch(path).group(1)))

    records = corpus.load_articles(data)
    if len(records) != report["records"]:
        raise PipelineError("Validated corpus count does not match updater report")
    if len(additions) != report["added"]:
        raise PipelineError("New article count does not match updater report")
    validated_additions = {
        path: _validated_new_article(data / path, ARTICLE_RE.fullmatch(path).group(1))
        for path in additions
    }

    _require_remote_data_sha(data, start_sha)
    if dry_run:
        return {
            "status": "dry-run", "changed": bool(additions), "added": len(additions),
            "data_sha": start_sha, "records": len(records),
        }
    if not additions:
        return {
            "status": "no-change", "changed": False, "added": 0,
            "data_sha": start_sha, "records": len(records),
        }

    _git(data, "add", "--", *additions)
    staged = _git(data, "diff", "--cached", "--name-status").stdout.splitlines()
    expected = ["A\t" + path for path in additions]
    if (sorted(staged) != sorted(expected)
            or any(_staged_bytes(data, path) != validated_additions[path] for path in additions)):
        _git(data, "reset", "--", *additions)
        raise PipelineError("Staged data is not exactly the validated article additions")
    _git(data, "diff", "--cached", "--check")
    if _git(data, "rev-parse", "HEAD").stdout.strip() != start_sha:
        _git(data, "reset", "--", *additions)
        raise PipelineError("Data checkout moved before commit")
    expected_tree = _exact_sha(_git(data, "write-tree").stdout.strip(), "validated data tree")

    subject = "Add {} article{}".format(len(additions), "" if len(additions) == 1 else "s")
    body = "Source-Code-SHA: {}\nWorkflow-Run: {}".format(source_sha, run_url)
    new_sha = _exact_sha(
        _git(data, "commit-tree", expected_tree, "-p", start_sha,
             "-m", subject, "-m", body).stdout.strip(),
        "new data SHA",
    )
    _git(data, "update-ref", "-m", subject, "HEAD", new_sha, start_sha)
    if (_git(data, "rev-parse", "HEAD^").stdout.strip() != start_sha
            or _git(data, "rev-parse", "HEAD^{tree}").stdout.strip() != expected_tree):
        raise PipelineError("Committed data does not match the validated tree and base")

    # This explicit preflight gives a clear error.  The normal, non-forced push
    # remains the authoritative race guard if the remote advances afterwards.
    _require_remote_data_sha(data, start_sha)
    _git(data, "push", "--porcelain", "origin", "HEAD:refs/heads/data")
    if _remote_data_sha(data) != new_sha:
        raise PipelineError("Remote data branch does not match the pushed commit")
    return {
        "status": "published", "changed": True, "added": len(additions),
        "data_sha": new_sha, "records": len(records),
    }


def export_exact(code_dir: Path, data_dir: Path, output_dir: Path, code_sha: str,
                 data_sha: str, minimal_only: bool = False,
                 export_function=corpus_export.export_corpus) -> dict:
    """Export a clean exact data commit, removing fresh output on any failure."""
    code = Path(code_dir).resolve()
    data = Path(data_dir).resolve()
    requested_output = Path(output_dir)
    if requested_output.is_symlink() or requested_output.exists():
        raise PipelineError("Export output directory must not already exist or be a symlink")
    output = requested_output.resolve()
    if _is_relative_to(output, data):
        raise PipelineError("Export output directory must be outside the data checkout")
    code_sha = _exact_sha(code_sha, "code SHA")
    data_sha = _exact_sha(data_sha, "data SHA")
    if _git(code, "rev-parse", "HEAD").stdout.strip() != code_sha:
        raise PipelineError("Code checkout does not match the requested SHA")
    if _status_entries(code):
        raise PipelineError("Exact code checkout must be clean before export")
    if _git(data, "rev-parse", "HEAD").stdout.strip() != data_sha:
        raise PipelineError("Data checkout does not match the requested SHA")
    if _status_entries(data):
        raise PipelineError("Exact data checkout must be clean before export")
    try:
        with tempfile.TemporaryDirectory(prefix="data-pipeline-export-") as temporary:
            snapshot = Path(temporary) / "data"
            _materialize_commit(data, data_sha, snapshot)
            return export_function(
                snapshot, output, include_full=not minimal_only,
                include_archive=not minimal_only, code_sha=code_sha, data_sha=data_sha,
            )
    except Exception as exc:
        shutil.rmtree(output, ignore_errors=True)
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError("Export failed: {}".format(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="validate, commit, and normally push additions")
    publish.add_argument("--data-dir", type=Path, required=True)
    publish.add_argument("--report", type=Path, required=True)
    publish.add_argument("--start-sha", required=True)
    publish.add_argument("--source-sha", required=True)
    publish.add_argument("--run-url", required=True)
    publish.add_argument("--dry-run", action="store_true")

    exporter = commands.add_parser("export", help="export from clean exact-SHA checkouts")
    exporter.add_argument("--code-dir", type=Path, required=True)
    exporter.add_argument("--data-dir", type=Path, required=True)
    exporter.add_argument("--output-dir", type=Path, required=True)
    exporter.add_argument("--code-sha", required=True)
    exporter.add_argument("--data-sha", required=True)
    exporter.add_argument("--minimal-only", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "publish":
            result = publish_changes(
                args.data_dir, args.report, args.start_sha, args.source_sha,
                args.run_url, args.dry_run,
            )
        else:
            result = export_exact(
                args.code_dir, args.data_dir, args.output_dir, args.code_sha,
                args.data_sha, args.minimal_only,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PipelineError, corpus.CorpusError, corpus_export.ExportError, OSError) as exc:
        print("data-pipeline: error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
