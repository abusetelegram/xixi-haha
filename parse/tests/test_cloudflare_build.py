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
        self.source = self.root / "source with spaces"
        self.data = self.root / "data with spaces"
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
        self._write_uv(tool, "0.12.18")
        self._write_uv(self.bin / "uv-template", "0.12.18")
        (self.bin / "python3").symlink_to(sys.executable)
        python = self.bin / "python3.13"
        python.write_text("""#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
if args == ['--version']:
    print('Python 3.13.3'); raise SystemExit
if args[:2] == ['-m', 'venv'] and len(args) == 3:
    target = pathlib.Path(args[2]) / 'bin'
    target.mkdir(parents=True)
    shutil.copy2(__file__, target / 'python')
    (target / 'python').chmod(0o755)
    raise SystemExit
if args[:2] == ['-m', 'pip']:
    with open(os.environ['BUILD_BOOTSTRAP_LOG'], 'a', encoding='utf-8') as log:
        log.write(' '.join(args) + '\\n')
    if os.environ.get('BOOTSTRAP_INSTALL_FAIL') == '1':
        raise SystemExit(23)
    target = pathlib.Path(__file__).with_name('uv')
    body = pathlib.Path(os.environ['UV_TOOL_TEMPLATE']).read_text(encoding='utf-8')
    target.write_text(body.replace('uv 0.12.18', 'uv ' + os.environ.get('BOOTSTRAP_UV_VERSION', '0.12.18')), encoding='utf-8')
    target.chmod(0o755)
    raise SystemExit
raise SystemExit('unexpected python3.13 invocation: ' + repr(args))
""", encoding="utf-8")
        node = self.bin / "node"
        node.write_text("#!/bin/sh\nprintf '%s\\n' v22.20.0\n", encoding="utf-8")
        npm = self.bin / "npm"
        npm.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$BUILD_TEST_LOG\"\n", encoding="utf-8")
        for path in (tool, python, node, npm):
            path.chmod(0o755)

    @staticmethod
    def _write_uv(tool, version):
        tool.write_text("""#!/usr/bin/env python3
import hashlib, json, os, pathlib, shutil, subprocess, sys
args = sys.argv[1:]
with open(os.environ['BUILD_UV_LOG'], 'a', encoding='utf-8') as log:
    log.write(sys.argv[0] + '\\t' + ' '.join(args) + '\\n')
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
""".replace("uv 0.12.18", "uv " + version), encoding="utf-8")
        tool.chmod(0o755)

    def run_build(self, extra_env=None):
        log = self.root / "commands.log"
        uv_log = self.root / "uv.log"
        bootstrap_log = self.root / "bootstrap.log"
        env = os.environ.copy()
        env.update({
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "DATA_REPOSITORY_URL": str(self.data),
            "BUILD_TEST_LOG": str(log),
            "BUILD_UV_LOG": str(uv_log),
            "BUILD_BOOTSTRAP_LOG": str(bootstrap_log),
            "UV_TOOL_TEMPLATE": str(self.bin / "uv-template"),
        })
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [str(self.source / "worker" / "scripts" / "build-cloudflare.sh")],
            cwd=self.root, env=env, capture_output=True, text=True)
        return result, log, uv_log, bootstrap_log

    def test_offline_build_pins_provenance_is_deterministic_and_never_deploys(self):
        first, log, uv_log, bootstrap_log = self.run_build()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(bootstrap_log.exists())
        selected = str((self.bin / "uv").resolve())
        uv_calls = uv_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(uv_calls)
        self.assertTrue(all(line.startswith(selected + "\t") for line in uv_calls))
        self.assertTrue(any("\tsync --locked --python 3.13" in line for line in uv_calls))
        self.assertTrue(any("\trun --frozen --offline --python 3.13" in line for line in uv_calls))
        assets = self.source / "worker" / "generated-assets"
        first_snapshot = {
            str(path.relative_to(assets)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in assets.rglob("*") if path.is_file()
        }
        metadata = json.loads((assets / "worker-data.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["sourceSha"], self.source_sha)
        self.assertEqual(metadata["dataSha"], self.data_sha)
        second, log, _, _ = self.run_build()
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

    def test_missing_uv_bootstraps_pinned_uv_and_uses_only_selected_executable(self):
        (self.bin / "uv").unlink()
        result, _, uv_log, bootstrap_log = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        pip_call = bootstrap_log.read_text(encoding="utf-8")
        self.assertIn("--isolated", pip_call)
        self.assertIn("--index-url https://pypi.org/simple", pip_call)
        self.assertIn("--timeout 30 --retries 2 uv==0.12.18", pip_call)
        calls = uv_log.read_text(encoding="utf-8").splitlines()
        selected = calls[0].split("\t", 1)[0]
        self.assertIn("xixi-haha-uv.", selected)
        self.assertTrue(all(line.startswith(selected + "\t") for line in calls))
        self.assertFalse(Path(selected).exists())

    def test_parent_path_uv_does_not_leak_into_missing_uv_fixture(self):
        (self.bin / "uv").unlink()
        host_bin = self.root / "host bin"
        host_bin.mkdir()
        host_uv = host_bin / "uv"
        self._write_uv(host_uv, "0.12.18")
        original_path = os.environ.get("PATH")
        os.environ["PATH"] = str(host_bin) + os.pathsep + (original_path or "")
        try:
            result, _, uv_log, bootstrap_log = self.run_build()
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(bootstrap_log.exists())
        calls = uv_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls)
        self.assertTrue(all(not line.startswith(str(host_uv) + "\t") for line in calls))

    def test_wrong_ambient_uv_bootstraps_instead_of_using_it_for_locked_commands(self):
        self._write_uv(self.bin / "uv", "0.12.17")
        result, _, uv_log, bootstrap_log = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(bootstrap_log.exists())
        calls = uv_log.read_text(encoding="utf-8").splitlines()
        ambient = str((self.bin / "uv").resolve())
        self.assertEqual(calls[0], ambient + "\t--version")
        selected = calls[1].split("\t", 1)[0]
        self.assertNotEqual(selected, ambient)
        self.assertTrue(all(line.startswith(selected + "\t") for line in calls[1:]))

    def test_bootstrap_install_failure_stops_build(self):
        (self.bin / "uv").unlink()
        result, _, _, bootstrap_log = self.run_build({"BOOTSTRAP_INSTALL_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to install uv 0.12.18", result.stderr)
        self.assertTrue(bootstrap_log.exists())

    def test_bootstrap_rejects_wrong_installed_version(self):
        (self.bin / "uv").unlink()
        result, _, uv_log, _ = self.run_build({"BOOTSTRAP_UV_VERSION": "0.12.17"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed uv did not report version 0.12.18", result.stderr)
        self.assertIn("\t--version", uv_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
