#!/usr/bin/env python3
"""Fail closed unless an exported Worker generation is bounded and provenance-exact."""

import hashlib
import json
from pathlib import Path
import re
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
CONTENT_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILES = 20_000
MAX_BYTES = 25 * 1024 * 1024


def fail(message):
    raise SystemExit("validate-deployment: " + message)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail("invalid JSON {}: {}".format(path, exc))


def hash_file(path):
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as content:
            while True:
                chunk = content.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size >= MAX_BYTES:
                    fail("asset must be smaller than 25 MiB: {}".format(path))
                digest.update(chunk)
    except OSError as exc:
        fail("cannot read declared asset {}: {}".format(path, exc))
    return size, digest.hexdigest()


def descriptor_path(descriptor, label, version_root, seen):
    if not isinstance(descriptor, dict):
        fail("{} descriptor must be an object".format(label))
    relative = descriptor.get("path")
    size = descriptor.get("bytes")
    checksum = descriptor.get("sha256")
    if not isinstance(relative, str) or not relative:
        fail("{} descriptor path must be a nonempty string".format(label))
    path = Path(relative)
    if (path.is_absolute() or "\\" in relative or path.as_posix() != relative
            or "." in path.parts or ".." in path.parts
            or not relative.startswith(version_root + "/")):
        fail("{} descriptor path is unsafe or not version-pinned".format(label))
    if relative in seen:
        fail("duplicate descriptor path: {}".format(relative))
    seen.add(relative)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        fail("{} descriptor bytes must be a nonnegative integer".format(label))
    if not isinstance(checksum, str) or CONTENT_SHA.fullmatch(checksum) is None:
        fail("{} descriptor sha256 must be lowercase 64-hex".format(label))
    return relative, size, checksum


def verify_descriptor(root, descriptor, label, version_root, seen):
    relative, expected_size, expected_checksum = descriptor_path(
        descriptor, label, version_root, seen)
    actual_size, actual_checksum = hash_file(root / relative)
    if actual_size != expected_size:
        fail("{} size does not match manifest".format(label))
    if actual_checksum != expected_checksum:
        fail("{} checksum does not match manifest".format(label))
    return relative


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

    version_root = "_data/{}".format(data_sha)
    declared = {"worker-data.json", expected_manifest}
    seen = set()
    index = manifest.get("paragraphIndex")
    declared.add(verify_descriptor(root, index, "paragraph index", version_root, seen))
    sharding = manifest.get("sharding")
    if not isinstance(sharding, dict):
        fail("sharding must be an object")
    shards = sharding.get("shards")
    shard_count = sharding.get("shardCount")
    if not isinstance(shards, list):
        fail("sharding shards must be an array")
    if (isinstance(shard_count, bool) or not isinstance(shard_count, int)
            or shard_count < 0 or shard_count != len(shards)):
        fail("shardCount must match the shard descriptor count")
    for number, shard in enumerate(shards):
        declared.add(verify_descriptor(
            root, shard, "shard {}".format(number), version_root, seen))
    actual = {str(path.relative_to(root)) for path in files}
    if actual != declared:
        fail("asset tree does not exactly match the manifest")
    print("validated {} public assets for source={} data={}".format(len(files), source_sha, data_sha))


if __name__ == "__main__":
    main(sys.argv)
