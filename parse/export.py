#!/usr/bin/env python3
"""Deterministic downloadable exports from the canonical article corpus.

The canonical data checkout is read-only to this tool.  Generated artifacts
must be written to a separate output directory.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile

import corpus


MINIMAL_NAME = "result-min.json"
FULL_NAME = "result.json"
ARCHIVE_NAME = "result-full.tgz"
PROVENANCE_NAME = "provenance.json"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class ExportError(ValueError):
    """An export request is unsafe or internally inconsistent."""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _absolute_lexical(path: Path) -> Path:
    """Return an absolute, normalized path without following symlinks."""
    return Path(os.path.abspath(os.fspath(path)))


def _contains_output_symlink(data: Path, output: Path) -> bool:
    """Check output-only path components without rejecting shared system aliases."""
    common = Path(os.path.commonpath((str(data), str(output))))
    candidate = common
    for part in output.relative_to(common).parts:
        candidate /= part
        if candidate.is_symlink():
            return True
    return False


def _validate_locations(data_dir: Path, output_dir: Path) -> tuple:
    data_lexical = _absolute_lexical(data_dir)
    output_lexical = _absolute_lexical(output_dir)
    if _is_relative_to(output_lexical, data_lexical):
        raise ExportError("Output directory must be outside the canonical data checkout")
    if _contains_output_symlink(data_lexical, output_lexical):
        raise ExportError("Output path must not contain symlinks")

    data = data_lexical.resolve()
    output = output_lexical.resolve()
    if _is_relative_to(output, data):
        raise ExportError("Output directory must be outside the canonical data checkout")
    return data, output


def _validate_provenance(code_sha, data_sha) -> None:
    if (code_sha is None) != (data_sha is None):
        raise ExportError("code_sha and data_sha must be supplied together")
    for name, value in (("code_sha", code_sha), ("data_sha", data_sha)):
        if value is not None and not SHA_PATTERN.fullmatch(value):
            raise ExportError("{} must be an exact lowercase 40- or 64-hex SHA".format(name))


def _write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_archive(path: Path, full_json: Path) -> None:
    size = full_json.stat().st_size
    with path.open("wb") as raw:
        with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                info = tarfile.TarInfo(FULL_NAME)
                info.size = size
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with full_json.open("rb") as source:
                    archive.addfile(info, source)
        raw.flush()
        os.fsync(raw.fileno())


def _digest(path: Path) -> dict:
    checksum = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
            size += len(block)
    return {"sha256": checksum.hexdigest(), "bytes": size}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_corpus(data_dir: Path, output_dir: Path, *, include_full: bool = False,
                  include_archive: bool = False, code_sha=None, data_sha=None) -> dict:
    """Validate the complete store, then atomically replace requested exports.

    ``result-min.json`` is always produced.  ``result.json`` and
    ``result-full.tgz`` are optional downloadable full-corpus forms.  Supplying
    both SHAs additionally writes ``provenance.json`` with checksums for every
    requested data artifact.
    """
    data, output = _validate_locations(data_dir, output_dir)
    _validate_provenance(code_sha, data_sha)

    # This is deliberately complete and happens before the output directory is
    # created or any existing artifact is touched.
    records = corpus.load_articles(data)
    minimal = {aid: corpus.minimal_record(record) for aid, record in records.items()}
    full = list(records.values())

    requested = [MINIMAL_NAME]
    if include_full:
        requested.append(FULL_NAME)
    if include_archive:
        requested.append(ARCHIVE_NAME)
    if code_sha is not None:
        requested.append(PROVENANCE_NAME)

    output.mkdir(parents=True, exist_ok=True)
    if not output.is_dir() or output.is_symlink():
        raise ExportError("Output path must be a real directory")
    for name in requested:
        destination = output / name
        if destination.exists() and (destination.is_symlink() or not destination.is_file()):
            raise ExportError("Export destination must be a regular file: {}".format(destination))

    with tempfile.TemporaryDirectory(prefix=".export-", dir=output) as temporary_name:
        temporary = Path(temporary_name)
        staged = {}
        minimal_path = temporary / MINIMAL_NAME
        _write_json(minimal_path, minimal)
        staged[MINIMAL_NAME] = minimal_path

        full_path = temporary / FULL_NAME
        if include_full or include_archive:
            _write_json(full_path, full)
        if include_full:
            staged[FULL_NAME] = full_path
        if include_archive:
            archive_path = temporary / ARCHIVE_NAME
            _write_archive(archive_path, full_path)
            staged[ARCHIVE_NAME] = archive_path

        artifacts = {name: _digest(staged[name]) for name in sorted(staged)}
        if code_sha is not None:
            provenance = {
                "schema": 1,
                "code_sha": code_sha,
                "data_sha": data_sha,
                "records": len(records),
                "artifacts": artifacts,
            }
            provenance_path = temporary / PROVENANCE_NAME
            _write_json(provenance_path, provenance)
            staged[PROVENANCE_NAME] = provenance_path

        # No named output is replaced until every requested file and checksum
        # has been generated successfully.
        for name in requested:
            os.replace(str(staged[name]), str(output / name))
        _fsync_directory(output)

    return {"records": len(records), "artifacts": artifacts}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full-json", action="store_true",
                        help="also produce result.json")
    parser.add_argument("--archive", action="store_true",
                        help="also produce deterministic result-full.tgz")
    parser.add_argument("--code-sha")
    parser.add_argument("--data-sha")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = export_corpus(
            args.data_dir, args.output_dir, include_full=args.full_json,
            include_archive=args.archive, code_sha=args.code_sha, data_sha=args.data_sha)
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (corpus.CorpusError, ExportError, OSError, tarfile.TarError) as exc:
        print("export: error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
