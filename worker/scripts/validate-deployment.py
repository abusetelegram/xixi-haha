#!/usr/bin/env python3
"""Fail closed unless an exported Worker generation is bounded and provenance-exact."""

import hashlib
import json
from pathlib import Path
import re
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_FILES = 20_000
MAX_BYTES = 25 * 1024 * 1024


def fail(message):
    raise SystemExit("validate-deployment: " + message)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail("invalid JSON {}: {}".format(path, exc))


def main(argv):
    if len(argv) != 4:
        fail("usage: validate-deployment.py ASSET_DIR SOURCE_SHA DATA_SHA")
    lexical_root = Path(argv[1]).absolute()
    source_sha, data_sha = argv[2:]
    if not SHA.fullmatch(source_sha) or not SHA.fullmatch(data_sha):
        fail("provenance must use exact lowercase commit SHAs")
    if lexical_root.is_symlink():
        fail("asset root must be a real directory")
    try:
        root = lexical_root.resolve(strict=True)
    except OSError:
        fail("asset root must be a real directory")
    if not root.is_dir():
        fail("asset root must be a real directory")

    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files or len(files) >= MAX_FILES:
        fail("asset file count must be between 1 and 19999")
    if any(path.is_symlink() for path in root.rglob("*")):
        fail("asset tree may not contain symlinks")
    for path in files:
        size = path.stat().st_size
        if size >= MAX_BYTES:
            fail("asset must be smaller than 25 MiB: {}".format(path.relative_to(root)))

    metadata = load_json(root / "worker-data.json")
    expected_manifest = "_data/{}/manifest.json".format(data_sha)
    if metadata.get("sourceSha") != source_sha or metadata.get("dataSha") != data_sha:
        fail("metadata provenance does not match the requested generation")
    if metadata.get("manifestPath") != expected_manifest:
        fail("metadata manifest path is not version-pinned")
    manifest_path = root / expected_manifest
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != metadata.get("manifestSha256"):
        fail("manifest checksum does not match metadata")
    manifest = load_json(manifest_path)
    if manifest.get("sourceSha") != source_sha or manifest.get("dataSha") != data_sha:
        fail("manifest provenance does not match metadata")

    declared = {"worker-data.json", expected_manifest}
    index = manifest.get("paragraphIndex", {})
    declared.add(index.get("path"))
    for shard in manifest.get("sharding", {}).get("shards", []):
        declared.add(shard.get("path"))
    actual = {str(path.relative_to(root)) for path in files}
    if None in declared or actual != declared:
        fail("asset tree does not exactly match the manifest")
    print("validated {} public assets for source={} data={}".format(len(files), source_sha, data_sha))


if __name__ == "__main__":
    main(sys.argv)
