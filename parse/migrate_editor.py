#!/usr/bin/env python3
"""Explicit offline migration/check for canonical stored editor values.

This is deliberately separate from the add-only updater.  Dry-run is the
default; mutation requires ``--apply`` and never invokes Git or the network.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from corpus import CorpusError, load_articles, normalize_editor, serialize_record


def migration_plan(data_dir: Path) -> tuple:
    """Return validated originals, editor-only replacements, and a summary."""
    originals = load_articles(data_dir)
    replacements = {}
    digest = hashlib.sha256()
    role_only = []
    unknown = []
    non_editor_changes = 0

    for aid, original in originals.items():
        migrated = dict(original)
        migrated["editor"] = normalize_editor(original["editor"])
        if normalize_editor(migrated["editor"]) != migrated["editor"]:
            raise CorpusError("Editor normalization is not idempotent for article {}".format(aid))
        for field in original:
            if field != "editor" and migrated[field] != original[field]:
                non_editor_changes += 1
        if migrated["editor"] != original["editor"]:
            replacements[aid] = serialize_record(migrated)
        if migrated["editor"] == "实习生":
            role_only.append(aid)
        if migrated["editor"] == "不明":
            unknown.append(aid)
        digest.update(aid.encode("ascii") + b"\0" + serialize_record(migrated))

    summary = {
        "records": len(originals),
        "changed": len(replacements),
        "unchanged": len(originals) - len(replacements),
        "non_editor_changes": non_editor_changes,
        "normalized_sha256": digest.hexdigest(),
        "role_only_review_ids": role_only,
        "unknown_editor_ids": unknown,
    }
    return originals, replacements, summary


def _require_expected(summary: dict, expected_records, expected_changed) -> None:
    if expected_records is not None and summary["records"] != expected_records:
        raise CorpusError("Expected {} records, found {}".format(
            expected_records, summary["records"]))
    if expected_changed is not None and summary["changed"] != expected_changed:
        raise CorpusError("Expected {} changed records, found {}".format(
            expected_changed, summary["changed"]))
    if summary["non_editor_changes"] != 0:
        raise CorpusError("Migration plan changes non-editor fields")


def apply_plan(data_dir: Path, replacements: dict) -> None:
    """Atomically replace each planned article; no other path is touched."""
    articles = Path(data_dir) / "articles"
    for aid in sorted(replacements, key=int):
        destination = articles / (aid + ".json")
        temporary = None
        mode = destination.stat().st_mode
        try:
            descriptor, name = tempfile.mkstemp(prefix="." + aid + ".", dir=articles)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(replacements[aid])
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(str(temporary), str(destination))
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="write the validated editor-only plan (default: dry-run)")
    parser.add_argument("--expect-records", type=int)
    parser.add_argument("--expect-changed", type=int)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        _, replacements, summary = migration_plan(args.data_dir)
        _require_expected(summary, args.expect_records, args.expect_changed)
        if args.apply:
            apply_plan(args.data_dir, replacements)
            _, remaining, after = migration_plan(args.data_dir)
            if remaining or after["normalized_sha256"] != summary["normalized_sha256"]:
                raise CorpusError("Post-migration validation did not reach the planned corpus")
        summary["mode"] = "apply" if args.apply else "dry-run"
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except (CorpusError, OSError) as exc:
        print("migrate-editor: error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
