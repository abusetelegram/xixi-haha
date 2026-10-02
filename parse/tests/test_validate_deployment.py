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
        data_root.mkdir(parents=True)
        index_path = data_root / "paragraph-index.bin"
        shard_path = data_root / "articles-000.json"
        index_path.write_bytes(b"tiny-index")
        shard_path.write_text("[]\n", encoding="utf-8")
        manifest = {
            "sourceSha": SOURCE_SHA,
            "dataSha": DATA_SHA,
            "paragraphIndex": {"path": str(index_path.relative_to(root))},
            "sharding": {"shards": [{"path": str(shard_path.relative_to(root))}]},
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
