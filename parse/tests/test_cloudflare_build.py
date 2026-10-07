import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "worker" / "scripts" / "build-cloudflare.sh"
VALIDATOR = ROOT / "worker" / "scripts" / "validate-deployment.py"


class CloudflareNativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.data = self.root / "data"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._init_source()
        self._init_data()
        self._write_fake_tools()

    @staticmethod
    def _git(directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), *args], check=True,
            capture_output=True, text=True).stdout.strip()

    def _init_source(self):
        (self.source / "worker" / "scripts").mkdir(parents=True)
        shutil.copy2(BUILD_SCRIPT, self.source / "worker" / "scripts" / BUILD_SCRIPT.name)
        shutil.copy2(VALIDATOR, self.source / "worker" / "scripts" / VALIDATOR.name)
        (self.source / ".gitignore").write_text(
            "worker/generated-assets/\nworker/node_modules/\nworker/dist-deployment/\n",
            encoding="utf-8")
        self._git(self.source, "init", "-b", "master")
        self._git(self.source, "config", "user.name", "test")
        self._git(self.source, "config", "user.email", "test@example.invalid")
        self._git(self.source, "add", ".")
        self._git(self.source, "commit", "-m", "fixture source")
        self.source_sha = self._git(self.source, "rev-parse", "HEAD")

    def _init_data(self):
        (self.data / "articles").mkdir(parents=True)
        (self.data / "articles" / "1.json").write_text("{}\n", encoding="utf-8")
        self._git(self.data, "init", "-b", "data")
        self._git(self.data, "config", "user.name", "test")
        self._git(self.data, "config", "user.email", "test@example.invalid")
        self._git(self.data, "add", ".")
        self._git(self.data, "commit", "-m", "fixture data")
        self.data_sha = self._git(self.data, "rev-parse", "HEAD")

    def _write_fake_tools(self):
        tool = self.bin / "uv"
        tool.write_text("""#!/usr/bin/env python3
import hashlib, json, os, pathlib, shutil, subprocess, sys
args = sys.argv[1:]
if args == ['--version']:
    print('uv 0.12.18'); raise SystemExit
if args and args[0] == 'sync':
    raise SystemExit
command = args[args.index('python') + 1:]
script = command[0]
if script.endswith('corpus.py') or script.endswith('generate-test-assets.py'):
    raise SystemExit
if script.endswith('validate-deployment.py'):
    raise SystemExit(subprocess.run([sys.executable, *command]).returncode)
if script.endswith('export_worker.py'):
    def value(flag): return command[command.index(flag) + 1]
    out = pathlib.Path(value('--output-dir'))
    if out.exists(): shutil.rmtree(out)
    source, data = value('--source-sha'), value('--data-sha')
    version = out / '_data' / data
    version.mkdir(parents=True)
    index = b'fixture-index'
    shard = b'[]\\n'
    (version / 'paragraph-index.bin').write_bytes(index)
    (version / 'shards').mkdir()
    (version / 'shards' / '000.json').write_bytes(shard)
    desc = lambda path, body: {'path': path, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
    manifest = {'sourceSha': source, 'dataSha': data,
      'paragraphIndex': desc(f'_data/{data}/paragraph-index.bin', index),
      'sharding': {'shardCount': 1, 'shards': [desc(f'_data/{data}/shards/000.json', shard)]}}
    body = (json.dumps(manifest, sort_keys=True) + '\\n').encode()
    (version / 'manifest.json').write_bytes(body)
    metadata = {'sourceSha': source, 'dataSha': data,
      'manifestPath': f'_data/{data}/manifest.json', 'manifestSha256': hashlib.sha256(body).hexdigest()}
    (out / 'worker-data.json').write_text(json.dumps(metadata, sort_keys=True) + '\\n')
    raise SystemExit
raise SystemExit('unexpected uv invocation: ' + repr(args))
""", encoding="utf-8")
        node = self.bin / "node"
        node.write_text("#!/bin/sh\nprintf '%s\\n' v22.20.0\n", encoding="utf-8")
        npm = self.bin / "npm"
        npm.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$BUILD_TEST_LOG\"\n", encoding="utf-8")
        for path in (tool, node, npm):
            path.chmod(0o755)

    def run_build(self):
        log = self.root / "commands.log"
        env = os.environ.copy()
        env.update({
            "PATH": str(self.bin) + os.pathsep + env["PATH"],
            "DATA_REPOSITORY_URL": str(self.data),
            "BUILD_TEST_LOG": str(log),
        })
        result = subprocess.run(
            [str(self.source / "worker" / "scripts" / "build-cloudflare.sh")],
            cwd=self.root, env=env, capture_output=True, text=True)
        return result, log

    def test_offline_build_pins_provenance_is_deterministic_and_never_deploys(self):
        first, log = self.run_build()
        self.assertEqual(first.returncode, 0, first.stderr)
        assets = self.source / "worker" / "generated-assets"
        first_snapshot = {
            str(path.relative_to(assets)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in assets.rglob("*") if path.is_file()
        }
        metadata = json.loads((assets / "worker-data.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["sourceSha"], self.source_sha)
        self.assertEqual(metadata["dataSha"], self.data_sha)
        second, log = self.run_build()
        self.assertEqual(second.returncode, 0, second.stderr)
        second_snapshot = {
            str(path.relative_to(assets)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in assets.rglob("*") if path.is_file()
        }
        self.assertEqual(second_snapshot, first_snapshot)
        self.assertEqual(self._git(self.source, "status", "--porcelain=v1"), "")
        commands = log.read_text(encoding="utf-8")
        self.assertIn("run package:deployment", commands)
        self.assertNotIn("deploy --config", commands)


if __name__ == "__main__":
    unittest.main()
