import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE_SHA = "2" * 40
DATA_SHA = "1" * 40
VALIDATOR = Path(__file__).resolve().parents[2] / "worker" / "scripts" / "validate-deployment.py"


class DeploymentValidatorRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self._write_valid_assets(self.assets)

    @staticmethod
    def _write_valid_assets(root):
        data_root = root / "_data" / DATA_SHA
        data_root.mkdir(parents=True, exist_ok=True)
        index_path = data_root / "paragraph-index.bin"
        shard_path = data_root / "articles-000.json"
        index_path.write_bytes(b"tiny-index")
        shard_path.write_text("[]\n", encoding="utf-8")
        manifest = {
            "sourceSha": SOURCE_SHA,
            "dataSha": DATA_SHA,
            "paragraphIndex": {
                "path": str(index_path.relative_to(root)),
                "bytes": len(index_path.read_bytes()),
                "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
            },
            "sharding": {
                "shardCount": 1,
                "shards": [{
                    "path": str(shard_path.relative_to(root)),
                    "bytes": len(shard_path.read_bytes()),
                    "sha256": hashlib.sha256(shard_path.read_bytes()).hexdigest(),
                }],
            },
        }
        manifest_path = data_root / "manifest.json"
        manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        metadata = {
            "sourceSha": SOURCE_SHA,
            "dataSha": DATA_SHA,
            "manifestPath": str(manifest_path.relative_to(root)),
            "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        (root / "worker-data.json").write_text(
            json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _snapshot(root):
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
        }

    def validate(self, root):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(root), SOURCE_SHA, DATA_SHA],
            check=False, capture_output=True, text=True)

    def assert_real_directory_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset root must be a real directory", result.stderr)

    def test_valid_real_tree_passes_without_altering_assets(self):
        before = self._snapshot(self.assets)
        result = self.validate(self.assets)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validated 4 public assets", result.stdout)
        self.assertEqual(self._snapshot(self.assets), before)

    def test_same_length_shard_corruption_is_rejected(self):
        shard = self.assets / "_data" / DATA_SHA / "articles-000.json"
        shard.write_text("{}\n", encoding="utf-8")
        result = self.validate(self.assets)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr)

    def test_same_length_index_corruption_is_rejected(self):
        index = self.assets / "_data" / DATA_SHA / "paragraph-index.bin"
        content = index.read_bytes()
        index.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        result = self.validate(self.assets)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr)

    def test_declared_length_mismatch_is_rejected(self):
        self._mutate_manifest(lambda manifest: manifest["paragraphIndex"].update(
            bytes=manifest["paragraphIndex"]["bytes"] + 1))
        result = self.validate(self.assets)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size", result.stderr)

    def test_missing_or_malformed_descriptor_fields_are_rejected(self):
        mutations = (
            lambda manifest: manifest["paragraphIndex"].pop("bytes"),
            lambda manifest: manifest["paragraphIndex"].update(bytes=True),
            lambda manifest: manifest["paragraphIndex"].update(bytes=-1),
            lambda manifest: manifest["sharding"]["shards"][0].update(sha256="A" * 64),
            lambda manifest: manifest["sharding"]["shards"][0].update(path="../escape"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self._write_valid_assets(self.assets)
                self._mutate_manifest(mutate)
                self.assertNotEqual(self.validate(self.assets).returncode, 0)

    def test_duplicate_descriptor_paths_are_rejected(self):
        def duplicate(manifest):
            manifest["sharding"]["shards"].append(dict(manifest["sharding"]["shards"][0]))
            manifest["sharding"]["shardCount"] = 2
        self._mutate_manifest(duplicate)
        result = self.validate(self.assets)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr)

    def test_shard_count_must_match_descriptors(self):
        self._mutate_manifest(lambda manifest: manifest["sharding"].update(shardCount=2))
        result = self.validate(self.assets)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shardCount", result.stderr)

    def _mutate_manifest(self, mutate):
        manifest_path = self.assets / "_data" / DATA_SHA / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        metadata_path = self.assets / "worker-data.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")

    def test_root_symlink_is_rejected_without_altering_target(self):
        before = self._snapshot(self.assets)
        link = self.root / "asset-link"
        os.symlink(self.assets, link)
        self.assert_real_directory_failure(self.validate(link))
        self.assertTrue(link.is_symlink())
        self.assertEqual(self._snapshot(self.assets), before)

    def test_dangling_root_symlink_is_rejected_without_creating_target(self):
        target = self.root / "does-not-exist"
        link = self.root / "dangling-link"
        os.symlink(target, link)
        self.assert_real_directory_failure(self.validate(link))
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())

    def test_file_root_is_rejected_without_altering_file(self):
        root_file = self.root / "not-a-directory"
        root_file.write_bytes(b"unchanged")
        self.assert_real_directory_failure(self.validate(root_file))
        self.assertEqual(root_file.read_bytes(), b"unchanged")

    def test_missing_root_is_rejected_without_creating_it(self):
        missing = self.root / "missing"
        self.assert_real_directory_failure(self.validate(missing))
        self.assertFalse(missing.exists())

    def test_symlinked_ancestor_is_allowed_when_root_itself_is_real(self):
        before = self._snapshot(self.assets)
        ancestor = self.root / "ancestor-link"
        os.symlink(self.root, ancestor)
        result = self.validate(ancestor / self.assets.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._snapshot(self.assets), before)


if __name__ == "__main__":
    unittest.main()
