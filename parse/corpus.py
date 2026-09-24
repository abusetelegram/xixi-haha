#!/usr/bin/env python3
"""Canonical per-article corpus storage and legacy import tooling.

The canonical store is ``DATA/articles/<positive-decimal-id>.json``.  This
module deliberately has no network dependencies: acquisition and export are
separate seams.
"""

import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
from typing import Iterable, Mapping


FIELDS = ("id", "title", "date", "author", "editor", "article", "text")
MINIMAL_FIELDS = ("title", "date", "author", "editor", "text")
TEMPLATE_DIR = Path(__file__).resolve().parent / "data-branch-template"
MAX_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024


class CorpusError(ValueError):
    """Corpus input is malformed or would violate add-only storage rules."""


def normalize_id(value) -> str:
    """Normalize an import-boundary ID to positive, canonical decimal text."""
    if isinstance(value, bool):
        raise CorpusError("Article ID must be a positive decimal integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        number = int(value)
    else:
        raise CorpusError("Article ID must be a positive decimal integer: {!r}".format(value))
    if number <= 0:
        raise CorpusError("Article ID must be positive: {!r}".format(value))
    return str(number)


def canonical_id(value) -> str:
    """Require the exact string representation used in records and filenames."""
    normalized = normalize_id(value)
    if not isinstance(value, str) or value != normalized:
        raise CorpusError("Article ID is not canonical: {!r}".format(value))
    return normalized


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CorpusError("Duplicate JSON object key {!r}".format(key))
        result[key] = value
    return result


def _load_json_text(text: str, description: str):
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except CorpusError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CorpusError("Invalid JSON in {}: {}".format(description, exc)) from exc


def read_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CorpusError("Cannot read UTF-8 JSON {}: {}".format(path, exc)) from exc
    return _load_json_text(text, str(path))


def canonical_record(record, strict_content: bool = False, import_ids: bool = False) -> dict:
    """Validate and order one full record without changing its field values.

    ``import_ids`` permits integer/noncanonical decimal IDs only at a legacy
    boundary and converts them to canonical text.  Historical validation allows
    empty strings and empty ``text`` arrays.  ``strict_content`` is for newly
    fetched records and enforces the content guarantees of the current parser.
    """
    if not isinstance(record, dict):
        raise CorpusError("Article record must be a JSON object")
    if set(record) != set(FIELDS) or len(record) != len(FIELDS):
        raise CorpusError("Article record must contain exactly: {}".format(", ".join(FIELDS)))
    aid = normalize_id(record["id"]) if import_ids else canonical_id(record["id"])
    for field in FIELDS[1:-1]:
        if not isinstance(record[field], str):
            raise CorpusError("Article {} field {} must be a string".format(aid, field))
    text = record["text"]
    if not isinstance(text, list) or not all(isinstance(line, str) for line in text):
        raise CorpusError("Article {} text must be an array of strings".format(aid))
    if strict_content:
        for field in ("title", "date", "editor", "article"):
            if not record[field].strip():
                raise CorpusError("New article {} field {} must not be empty".format(aid, field))
        if not text or any(not line.strip() for line in text):
            raise CorpusError("New article {} must have nonempty text lines".format(aid))
    ordered = {"id": aid}
    for field in FIELDS[1:]:
        ordered[field] = record[field]
    return ordered


def minimal_record(record) -> dict:
    return {field: record[field] for field in MINIMAL_FIELDS}


def serialize_record(record, strict_content: bool = False) -> bytes:
    ordered = canonical_record(record, strict_content=strict_content)
    return (json.dumps(ordered, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _record_path(articles: Path, aid: str) -> Path:
    return articles / (canonical_id(aid) + ".json")


def load_articles(data_dir: Path, strict_content: bool = False) -> dict:
    """Load and completely validate a canonical store in numeric-ID order."""
    articles = Path(data_dir) / "articles"
    if not articles.exists():
        return {}
    if not articles.is_dir() or articles.is_symlink():
        raise CorpusError("{} must be a real directory".format(articles))
    found = {}
    normalized_names = {}
    named_paths = []
    try:
        children = list(articles.iterdir())
    except OSError as exc:
        raise CorpusError("Cannot enumerate {}: {}".format(articles, exc)) from exc
    # Inspect every name before opening content so aliases such as 1.json and
    # 01.json are reported as normalized-ID collisions regardless of directory
    # enumeration order.
    for path in children:
        if path.is_symlink() or not path.is_file():
            raise CorpusError("Unexpected non-file in articles: {}".format(path.name))
        match = re.fullmatch(r"([0-9]+)\.json", path.name)
        if not match:
            raise CorpusError("Unexpected article filename: {}".format(path.name))
        raw_id = match.group(1)
        normalized = normalize_id(raw_id)
        previous = normalized_names.get(normalized)
        if previous is not None:
            raise CorpusError(
                "Duplicate normalized article filenames: {} and {}".format(previous, path.name))
        normalized_names[normalized] = path.name
        named_paths.append((path, raw_id))
    for path, raw_id in named_paths:
        aid = canonical_id(raw_id)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise CorpusError("Cannot read UTF-8 article {}: {}".format(path, exc)) from exc
        record = canonical_record(_load_json_text(text, str(path)), strict_content=strict_content)
        if record["id"] != aid:
            raise CorpusError("Filename/record ID mismatch for {}".format(path.name))
        expected = serialize_record(record, strict_content=strict_content)
        if raw != expected:
            raise CorpusError("Article {} is not deterministic LF pretty JSON".format(path.name))
        found[aid] = record
    return {aid: found[aid] for aid in sorted(found, key=int)}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def writer_lock(data_dir: Path):
    """Coordinate all canonical writers through DATA/.update.lock."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".update.lock"
    try:
        handle = lock.open("x", encoding="utf-8", newline="\n")
    except FileExistsError as exc:
        raise CorpusError("{} exists; another writer may be running".format(lock)) from exc
    try:
        with handle:
            handle.write(str(os.getpid()) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _validated_batch(records: Iterable[Mapping], strict_content: bool) -> dict:
    batch = {}
    for source in records:
        record = canonical_record(source, strict_content=strict_content)
        aid = record["id"]
        if aid in batch:
            raise CorpusError("Duplicate normalized article ID {}".format(aid))
        batch[aid] = record
    return batch


def _preflight(existing: dict, batch: dict) -> list:
    pending = []
    for aid in sorted(batch, key=int):
        if aid in existing:
            if existing[aid] != batch[aid]:
                raise CorpusError("Refusing to modify existing article {}".format(aid))
        else:
            pending.append(aid)
    return pending


def _publish_validated(data_dir: Path, existing: dict, batch: dict) -> int:
    """Publish only absent records with hard-link no-replace semantics."""
    pending = _preflight(existing, batch)
    if not pending:
        return 0
    directory = Path(data_dir)
    articles = directory / "articles"
    articles.mkdir(parents=True, exist_ok=True)
    if articles.is_symlink():
        raise CorpusError("articles must not be a symlink")
    staging = directory / ".cache" / "publish"
    staging.mkdir(parents=True, exist_ok=True)
    staged = {}
    try:
        for aid in pending:
            descriptor, name = tempfile.mkstemp(prefix=aid + ".", suffix=".json", dir=staging)
            path = Path(name)
            staged[aid] = path
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(serialize_record(batch[aid]))
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
        _fsync_directory(staging)
        published = 0
        for aid in pending:
            destination = _record_path(articles, aid)
            created = False
            try:
                os.link(str(staged[aid]), str(destination))
                created = True
            except FileExistsError:
                # A non-cooperating writer raced us.  Identical content is an
                # idempotent success; anything else is an immutable-data breach.
                try:
                    current = destination.read_bytes()
                except OSError as exc:
                    raise CorpusError("Cannot verify raced article {}".format(aid)) from exc
                if current != serialize_record(batch[aid]):
                    raise CorpusError("Refusing to replace raced article {}".format(aid))
            if created:
                published += 1
        _fsync_directory(articles)
        return published
    finally:
        for path in staged.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def create_articles(data_dir: Path, records: Iterable[Mapping],
                    strict_content: bool = True) -> int:
    """Validate a whole batch, then add it atomically per file without overwrite.

    A crash may expose a complete subset of the batch, which is safe to rerun;
    an existing canonical file is never replaced or truncated.
    """
    directory = Path(data_dir)
    batch = _validated_batch(records, strict_content)

    with writer_lock(directory):
        existing = load_articles(directory)
        return _publish_validated(directory, existing, batch)


def _legacy_full_records(archive_path: Path) -> dict:
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != "result.json" or not members[0].isreg():
                raise CorpusError("Legacy archive must contain only the regular file result.json")
            member = members[0]
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise CorpusError("Legacy result.json is unreasonably large")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CorpusError("Cannot read result.json from legacy archive")
            with extracted, io.TextIOWrapper(extracted, encoding="utf-8") as handle:
                rows = json.load(handle, object_pairs_hook=_unique_object)
    except CorpusError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError) as exc:
        raise CorpusError("Invalid legacy archive {}: {}".format(archive_path, exc)) from exc
    if not isinstance(rows, list):
        raise CorpusError("Legacy full corpus must be a JSON array")
    result = {}
    for source in rows:
        record = canonical_record(source, import_ids=True)
        aid = record["id"]
        if aid in result:
            raise CorpusError("Duplicate normalized full article ID {}".format(aid))
        result[aid] = record
    return result


def _legacy_minimal_records(minimal_path: Path) -> dict:
    source = read_json(minimal_path)
    if not isinstance(source, dict):
        raise CorpusError("Legacy minimal corpus must be an ID-keyed object")
    result = {}
    for raw_id, record in source.items():
        aid = normalize_id(raw_id)
        if aid in result:
            raise CorpusError("Duplicate normalized minimal article ID {}".format(aid))
        if not isinstance(record, dict) or set(record) != set(MINIMAL_FIELDS):
            raise CorpusError("Minimal article {} must contain exactly: {}".format(
                aid, ", ".join(MINIMAL_FIELDS)))
        for field in MINIMAL_FIELDS[:-1]:
            if not isinstance(record[field], str):
                raise CorpusError(
                    "Minimal article {} field {} must be a string".format(aid, field))
        if (not isinstance(record["text"], list)
                or not all(isinstance(line, str) for line in record["text"])):
            raise CorpusError("Minimal article {} text must be an array of strings".format(aid))
        result[aid] = {field: record[field] for field in MINIMAL_FIELDS}
    return result


def load_legacy(archive_path: Path, minimal_path: Path) -> dict:
    """Validate both legacy artifacts completely and return canonical full records."""
    full = _legacy_full_records(Path(archive_path))
    minimal = _legacy_minimal_records(Path(minimal_path))
    if set(full) != set(minimal):
        missing_minimal = sorted(set(full) - set(minimal), key=int)
        missing_full = sorted(set(minimal) - set(full), key=int)
        raise CorpusError("Legacy ID sets differ (missing minimal: {}; missing full: {})".format(
            missing_minimal[:5], missing_full[:5]))
    for aid in sorted(full, key=int):
        if minimal_record(full[aid]) != minimal[aid]:
            raise CorpusError("Full/minimal projection mismatch for article {}".format(aid))
    return {aid: full[aid] for aid in sorted(full, key=int)}


def _template_payloads() -> dict:
    names = ("README.md", "schema.json", ".gitignore", "index.html")
    result = {}
    for name in names:
        path = TEMPLATE_DIR / name
        try:
            result[name] = path.read_bytes()
        except OSError as exc:
            raise CorpusError("Missing data-branch template {}".format(path)) from exc
    # Make template corruption fail before any target mutation.
    _load_json_text(result["schema.json"].decode("utf-8"), str(TEMPLATE_DIR / "schema.json"))
    return result


def _preflight_templates(data_dir: Path, payloads: dict) -> list:
    pending = []
    for name, content in payloads.items():
        destination = Path(data_dir) / name
        if destination.exists():
            if not destination.is_file() or destination.is_symlink():
                raise CorpusError(
                    "Template destination is not a regular file: {}".format(destination))
            if destination.read_bytes() != content:
                raise CorpusError("Refusing to replace existing {}".format(destination))
        else:
            pending.append(name)
    return pending


def _publish_templates(data_dir: Path, payloads: dict, pending: list) -> None:
    directory = Path(data_dir)
    for name in pending:
        destination = directory / name
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + name.replace("/", "_"), dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(str(temporary), str(destination))
            except FileExistsError as exc:
                if destination.read_bytes() != payloads[name]:
                    raise CorpusError("Refusing to replace raced {}".format(destination)) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    _fsync_directory(directory)


def initialize_data_root(data_dir: Path) -> int:
    """Create the static branch files without replacing existing files."""
    directory = Path(data_dir)
    payloads = _template_payloads()
    with writer_lock(directory):
        pending = _preflight_templates(directory, payloads)
        _publish_templates(directory, payloads, pending)
        (directory / "articles").mkdir(exist_ok=True)
        return len(pending)


def import_legacy(archive_path: Path, minimal_path: Path, data_dir: Path) -> dict:
    """Fully validate legacy input, then idempotently create the data root."""
    records = load_legacy(archive_path, minimal_path)  # No target mutation before this completes.
    directory = Path(data_dir)
    payloads = _template_payloads()
    with writer_lock(directory):
        template_pending = _preflight_templates(directory, payloads)
        existing = load_articles(directory)
        extras = sorted(set(existing) - set(records), key=int)
        if extras:
            raise CorpusError(
                "Data root has articles outside legacy import: {}".format(extras[:5]))
        pending = _preflight(existing, records)
        _publish_templates(directory, payloads, template_pending)
        (directory / "articles").mkdir(exist_ok=True)
        added = _publish_validated(directory, existing, records)
        validated = load_articles(directory)
        if validated != records:
            raise CorpusError("Post-import corpus does not exactly match legacy full records")
    return {"records": len(records), "added": added, "templates_added": len(template_pending)}


def validate_import(data_dir: Path, archive_path: Path, minimal_path: Path) -> dict:
    expected = load_legacy(archive_path, minimal_path)
    actual = load_articles(data_dir)
    if actual != expected:
        if set(actual) != set(expected):
            raise CorpusError("Canonical and legacy ID sets differ")
        for aid in sorted(expected, key=int):
            if actual[aid] != expected[aid]:
                raise CorpusError("Canonical/legacy full record mismatch for {}".format(aid))
        raise CorpusError("Canonical corpus differs from legacy corpus")
    return actual


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    importer = subparsers.add_parser(
        "import-legacy", help="import exact full/minimal legacy artifacts")
    importer.add_argument("--archive", type=Path, required=True)
    importer.add_argument("--minimal", type=Path, required=True)
    importer.add_argument("--data-dir", type=Path, required=True)

    validator = subparsers.add_parser(
        "validate", help="validate canonical articles and optional legacy parity")
    validator.add_argument("--data-dir", type=Path, required=True)
    validator.add_argument("--archive", type=Path)
    validator.add_argument("--minimal", type=Path)

    initializer = subparsers.add_parser("init", help="create static data-branch files")
    initializer.add_argument("--data-dir", type=Path, required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "import-legacy":
            summary = import_legacy(args.archive, args.minimal, args.data_dir)
        elif args.command == "validate":
            if (args.archive is None) != (args.minimal is None):
                raise CorpusError("--archive and --minimal must be provided together")
            records = (validate_import(args.data_dir, args.archive, args.minimal)
                       if args.archive is not None else load_articles(args.data_dir))
            summary = {"records": len(records),
                       "empty_text": sum(not record["text"] for record in records.values())}
        else:
            summary = {"templates_added": initialize_data_root(args.data_dir)}
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (CorpusError, OSError) as exc:
        print("corpus: error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
