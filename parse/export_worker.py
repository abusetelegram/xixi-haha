#!/usr/bin/env python3
"""Build bounded, immutable static assets for the Cloudflare Worker.

The exporter reuses :mod:`corpus` for canonical validation. It never writes to
or normalizes the source corpus and deliberately omits canonical ``article``
HTML from every emitted record.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Mapping

from corpus import CorpusError, load_articles

FORMAT_VERSION = 1
DEFAULT_SHARDS = 256
UINT32_MAX = (1 << 32) - 1
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_ASSET_FILES = 20_000
INDEX_MAGIC = b"XHPI"
INDEX_HEADER = struct.Struct(">4sIII")  # magic, format, record count, selectable count
INDEX_ENTRY = struct.Struct(">II")     # article id, cumulative selectable offset
SHA_RE = re.compile(r"[0-9a-f]{40}")
PROJECTED_FIELDS = ("id", "title", "date", "author", "editor", "text")
OPTIONAL_FIELDS = ("content_type", "media")
# ECMAScript WhiteSpace + LineTerminator code points. Keeping this explicit
# makes Python export ranks match the TypeScript runtime without normalization.
SELECTION_WHITESPACE = frozenset(
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff")
FIXED_OUTPUT_FILES = 3  # metadata, manifest, paragraph index


class ExportError(ValueError):
    """The requested export is invalid or could not be published safely."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _compact_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _require_sha(value: str, name: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ExportError("{} must be an exact lowercase 40-hex commit SHA".format(name))
    return value


def _git_head(directory: Path, name: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="ascii")
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        raise ExportError("Cannot verify {} checkout HEAD: {}".format(name, exc)) from exc
    return result.stdout.strip()


def verify_checkout(directory: Path, expected_sha: str, name: str) -> None:
    actual = _git_head(directory, name)
    if actual != expected_sha:
        raise ExportError("{} checkout HEAD {} does not match {}".format(name, actual, expected_sha))


def _project(record: Mapping) -> dict:
    projected = {field: record[field] for field in PROJECTED_FIELDS}
    present = [field for field in OPTIONAL_FIELDS if field in record]
    if present and len(present) != len(OPTIONAL_FIELDS):
        raise ExportError("Article {} has incomplete media fields".format(record.get("id")))
    for field in present:
        projected[field] = record[field]
    if "article" in projected:
        raise AssertionError("raw article HTML must never be projected")
    return projected


def is_selectable_paragraph(value: str) -> bool:
    """Return whether an exact paragraph payload contains a non-whitespace code point."""
    if not isinstance(value, str):
        raise ExportError("Paragraph must be a string")
    return any(character not in SELECTION_WHITESPACE for character in value)


def encode_paragraph_index(records) -> bytes:
    """Encode sorted IDs and cumulative offsets as network-byte-order uint32s."""
    if len(records) > UINT32_MAX:
        raise ExportError("Article count does not fit uint32")
    total = 0
    entries = []
    previous = 0
    for record in records:
        try:
            aid = int(record["id"])
            paragraphs = sum(is_selectable_paragraph(value) for value in record["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportError("Invalid projected article/index fields") from exc
        if aid <= previous or aid > UINT32_MAX:
            raise ExportError("Article IDs must be sorted unique positive uint32 values")
        if paragraphs < 0 or paragraphs > UINT32_MAX - total:
            raise ExportError("Cumulative paragraph count does not fit uint32")
        total += paragraphs
        entries.append(INDEX_ENTRY.pack(aid, total))
        previous = aid
    return INDEX_HEADER.pack(INDEX_MAGIC, FORMAT_VERSION, len(records), total) + b"".join(entries)


def decode_paragraph_index(content: bytes):
    """Strict decoder used by build validation and as a cross-language fixture oracle."""
    if len(content) < INDEX_HEADER.size:
        raise ExportError("Paragraph index is truncated")
    magic, version, count, total = INDEX_HEADER.unpack_from(content)
    expected_size = INDEX_HEADER.size + count * INDEX_ENTRY.size
    if magic != INDEX_MAGIC or version != FORMAT_VERSION or len(content) != expected_size:
        raise ExportError("Paragraph index header or length is invalid")
    entries = []
    previous_id = 0
    previous_offset = 0
    for position in range(count):
        aid, offset = INDEX_ENTRY.unpack_from(content, INDEX_HEADER.size + position * INDEX_ENTRY.size)
        if aid <= previous_id or offset < previous_offset or offset > total:
            raise ExportError("Paragraph index entries are not sorted/monotonic")
        entries.append((aid, offset))
        previous_id, previous_offset = aid, offset
    if (entries[-1][1] if entries else 0) != total:
        raise ExportError("Paragraph index final offset does not match total")
    return {"articleCount": count, "selectableParagraphCount": total, "entries": entries}


def _validate_shard_count(shard_count: int) -> None:
    if (isinstance(shard_count, bool) or not isinstance(shard_count, int)
            or shard_count <= 0 or shard_count > UINT32_MAX
            or shard_count & (shard_count - 1)):
        raise ExportError("shard count must be a positive power of two fitting uint32")
    if shard_count + FIXED_OUTPUT_FILES > MAX_ASSET_FILES:
        raise ExportError("shard count {} would produce {} files; static asset limit is {}".format(
            shard_count, shard_count + FIXED_OUTPUT_FILES, MAX_ASSET_FILES))


def _write_file(root: Path, relative: str, content: bytes, files: dict) -> None:
    if len(content) > MAX_ASSET_BYTES:
        raise ExportError("Static asset exceeds {} bytes: {}".format(
            MAX_ASSET_BYTES, relative))
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    files[relative] = {"bytes": len(content), "sha256": _sha256(content)}


def _build_stage(stage: Path, records: Mapping[str, dict], source_sha: str,
                 data_sha: str, shard_count: int) -> dict:
    projected = [_project(records[aid]) for aid in sorted(records, key=int)]
    index = encode_paragraph_index(projected)
    decoded = decode_paragraph_index(index)
    width = max(3, len(str(shard_count - 1)))
    version_root = Path("_data") / data_sha
    files = {}
    index_name = str(version_root / "paragraph-index.bin")
    _write_file(stage, index_name, index, files)

    shards = [[] for _ in range(shard_count)]
    for record in projected:
        shards[int(record["id"]) & (shard_count - 1)].append(record)
    shard_map = []
    for number, rows in enumerate(shards):
        relative = str(version_root / "shards" / (str(number).zfill(width) + ".json"))
        content = _compact_json(rows)
        _write_file(stage, relative, content, files)
        shard_map.append({"shard": number, "path": relative,
                          "recordCount": len(rows), **files[relative]})

    manifest = {
        "formatVersion": FORMAT_VERSION,
        "sourceSha": source_sha,
        "dataSha": data_sha,
        "counts": {"articles": len(projected),
                   "selectableArticles": sum(
                       any(is_selectable_paragraph(value) for value in row["text"])
                       for row in projected),
                   "sourceParagraphs": sum(len(row["text"]) for row in projected),
                   "selectableParagraphs": decoded["selectableParagraphCount"]},
        "sharding": {"algorithm": "uint32-id-bitmask", "shardCount": shard_count,
                     "mask": shard_count - 1, "recordsSortedBy": "numeric-id",
                     "shards": shard_map},
        "paragraphIndex": {
            "path": index_name, "byteOrder": "big-endian",
            "layout": "4-byte XHPI magic; uint32 formatVersion, recordCount, selectableParagraphCount; repeated uint32 articleId,cumulativeSelectableParagraphOffset",
            **files[index_name],
        },
    }
    manifest_name = str(version_root / "manifest.json")
    manifest_content = _compact_json(manifest)
    if len(manifest_content) > MAX_ASSET_BYTES:
        raise ExportError("Manifest exceeds static asset size limit")
    (stage / manifest_name).write_bytes(manifest_content)
    metadata = {
        "formatVersion": FORMAT_VERSION, "sourceSha": source_sha, "dataSha": data_sha,
        "manifestPath": manifest_name, "manifestSha256": _sha256(manifest_content),
    }
    metadata_content = _compact_json(metadata)
    if len(metadata_content) > MAX_ASSET_BYTES:
        raise ExportError("Deployment metadata exceeds static asset size limit")
    (stage / "worker-data.json").write_bytes(metadata_content)
    generated_count = 2 + len(files)
    if generated_count > MAX_ASSET_FILES:
        raise ExportError("Export has {} files; static asset limit is {}".format(
            generated_count, MAX_ASSET_FILES))
    return manifest


def _validate_stage(stage: Path, manifest: dict, records: Mapping[str, dict]) -> None:
    index_meta = manifest["paragraphIndex"]
    index_content = (stage / index_meta["path"]).read_bytes()
    if len(index_content) != index_meta["bytes"] or _sha256(index_content) != index_meta["sha256"]:
        raise ExportError("Paragraph index checksum/size mismatch")
    decoded = decode_paragraph_index(index_content)
    expected_ids = [int(aid) for aid in sorted(records, key=int)]
    if [entry[0] for entry in decoded["entries"]] != expected_ids:
        raise ExportError("Paragraph index IDs do not match canonical records")
    expected_offsets = []
    total = 0
    for aid in sorted(records, key=int):
        total += sum(is_selectable_paragraph(value) for value in records[aid]["text"])
        expected_offsets.append(total)
    if [entry[1] for entry in decoded["entries"]] != expected_offsets:
        raise ExportError("Paragraph index offsets do not match canonical records")

    seen = {}
    for shard in manifest["sharding"]["shards"]:
        content = (stage / shard["path"]).read_bytes()
        if len(content) != shard["bytes"] or _sha256(content) != shard["sha256"]:
            raise ExportError("Shard checksum/size mismatch: {}".format(shard["path"]))
        try:
            rows = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ExportError("Invalid generated shard JSON") from exc
        if len(rows) != shard["recordCount"]:
            raise ExportError("Shard record count mismatch")
        last = 0
        for row in rows:
            aid = int(row["id"])
            if aid <= last or aid & (manifest["sharding"]["shardCount"] - 1) != shard["shard"]:
                raise ExportError("Shard assignment or ordering mismatch")
            if set(row) not in (set(PROJECTED_FIELDS), set(PROJECTED_FIELDS + OPTIONAL_FIELDS)):
                raise ExportError("Generated field projection mismatch")
            if row != _project(records[row["id"]]):
                raise ExportError("Generated record differs from canonical projection")
            if aid in seen:
                raise ExportError("Duplicate generated article ID")
            seen[aid] = shard["shard"]
            last = aid
    if set(seen) != {int(aid) for aid in records}:
        raise ExportError("Generated shards do not contain every canonical record exactly once")


def _publish_directory(stage: Path, output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise ExportError("output directory may not be a symlink")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    backup = output_dir.parent / ("." + output_dir.name + ".old")
    if backup.exists() or backup.is_symlink():
        raise ExportError("stale export backup exists: {}".format(backup))
    moved_old = False
    try:
        if output_dir.exists():
            os.replace(str(output_dir), str(backup))
            moved_old = True
        os.replace(str(stage), str(output_dir))
    except BaseException:
        if moved_old and not output_dir.exists():
            os.replace(str(backup), str(output_dir))
        raise
    if moved_old:
        shutil.rmtree(backup)


def export_worker(data_dir: Path, output_dir: Path, source_sha: str, data_sha: str,
                  shard_count: int = DEFAULT_SHARDS, verify_refs: bool = True) -> dict:
    data_dir = Path(data_dir).resolve()
    raw_output = Path(output_dir)
    output_dir = raw_output.parent.resolve() / raw_output.name
    source_sha = _require_sha(source_sha, "source SHA")
    data_sha = _require_sha(data_sha, "data SHA")
    _validate_shard_count(shard_count)
    if output_dir == data_dir or data_dir in output_dir.parents:
        raise ExportError("output directory must be outside the canonical data checkout")
    if verify_refs:
        verify_checkout(data_dir, data_sha, "data")
        verify_checkout(Path(__file__).resolve().parents[1], source_sha, "source")
    try:
        records = load_articles(data_dir)
    except CorpusError as exc:
        raise ExportError(str(exc)) from exc
    stage = Path(tempfile.mkdtemp(prefix="." + output_dir.name + ".stage-",
                                  dir=str(output_dir.parent))) if output_dir.parent.exists() else None
    if stage is None:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="." + output_dir.name + ".stage-",
                                      dir=str(output_dir.parent)))
    try:
        manifest = _build_stage(stage, records, source_sha, data_sha, shard_count)
        _validate_stage(stage, manifest, records)
        _publish_directory(stage, output_dir)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--data-sha", required=True)
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    args = parser.parse_args(argv)
    try:
        manifest = export_worker(args.data_dir, args.output_dir, args.source_sha,
                                 args.data_sha, args.shards)
    except (ExportError, OSError) as exc:
        parser.exit(1, "export_worker: {}\n".format(exc))
    print(json.dumps({"output": str(args.output_dir), **manifest["counts"],
                      "shards": args.shards}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
