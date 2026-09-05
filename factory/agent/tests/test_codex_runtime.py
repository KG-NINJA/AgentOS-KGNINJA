"""Offline routing and failure tests: subprocesses use recording stubs only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "factory/agent"))
import codex_runtime as runtime
import codex_daemon as daemon
import codex_app_client as client

CANDIDATE = {"FACTORY_CODEX_PROFILE": "gpt6", "FACTORY_CODEX_EFFORT": "high"}


class RoutingTests(unittest.TestCase):
    def test_candidate_all_roles_and_preserved_arguments(self):
        for role in runtime.ROLES:
            args = ["-a", "never", "exec", "--sandbox", "workspace-write", "-C", "/tmp/space dir", "a $literal prompt"]
            cmd = runtime.command(role, args, CANDIDATE)
            self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6-astra")
            self.assertEqual(cmd[-len(args):], args)
            self.assertIn('model_reasoning_effort="high"', cmd)

    def test_legacy_retains_workload_defaults(self):
        self.assertEqual(runtime.command("repair", ["exec"], {}), ["codex", "exec"])
        self.assertEqual(runtime.command("interpretation", ["exec"], {}), ["codex", "exec"])
        self.assertIn("gpt-5.3-codex", runtime.command("generation", ["exec"], {}))

    def test_bad_or_missing_effort_and_profile_fail_closed(self):
        for effort in (None, "", "none", "minimal", "ultra", 'high"; bogus=true'):
            env = {"FACTORY_CODEX_PROFILE": "gpt6"}
            if effort is not None:
                env["FACTORY_CODEX_EFFORT"] = effort
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                runtime.command("repair", ["exec"], env)
        with self.assertRaises(ValueError):
            runtime.selection({"FACTORY_CODEX_PROFILE": "typo"})

    def test_daemon_rejects_mismatched_queue_profile_without_rpc(self):
        app = Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CANDIDATE), patch.object(daemon, "ensure_safe_target", return_value=(True, "")):
            req = {"target_dir": tmp, "validate_log_path": str(Path(tmp) / "missing")}
            ok, reason, _ = daemon.run_repair(app, req, "untrusted", "workspace-write", 10)
            self.assertFalse(ok)
            self.assertEqual(reason, "runtime-selection-mismatch")
            app.request.assert_not_called()

    def test_daemon_uses_candidate_command(self):
        app = Mock()
        app.request.return_value = {"exitCode": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CANDIDATE), patch.object(daemon, "ensure_safe_target", return_value=(True, "")):
            req = {"target_dir": tmp, "validate_log_path": str(Path(tmp) / "missing"), "runtime_selection": runtime.selection(CANDIDATE)}
            self.assertTrue(daemon.run_repair(app, req, "untrusted", "workspace-write", 10)[0])
            self.assertIn("gpt-6-astra", app.request.call_args.args[1]["command"])

    def test_app_client_uses_candidate_command(self):
        rpc = Mock()
        rpc.request.return_value = {"exitCode": 0}
        session = Mock()
        session.get_session.return_value = "test-session"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CANDIDATE), patch.object(client, "FifoRpcClient", return_value=rpc), patch.object(client, "SessionManager", return_value=session):
            args = argparse.Namespace(root=tmp, target_dir=tmp, session_max_idle_sec=10, app_id="test", timeout=10, fail_log="", sandbox_mode="workspace-write", approval_policy="untrusted")
            self.assertEqual(client.run_repair(args), 0)
            self.assertIn("gpt-6-astra", rpc.request.call_args.args[1]["command"])

    def test_shell_repair_and_parser_use_candidate_or_stop(self):
        for role, relative in (("repair", "factory/repair/codex_fix.sh"), ("interpretation", "factory/parser/interpret.sh")):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for source in (relative, "factory/agent/codex_runtime.py"):
                    dest = root / source
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / source, dest)
                (root / "bin").mkdir()
                (root / "target").mkdir()
                (root / "queue").mkdir()
                (root / "runtime").mkdir()
                (root / "queue/task.md").write_text("Build a useful dashboard")
                stub = root / "bin/codex"
                stub.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\nsys.exit(42)\n")
                stub.chmod(0o755)
                env = dict(os.environ, **CANDIDATE, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"], CAPTURE=str(root / "capture.json"))
                args = ["bash", str(root / relative)] + ([str(root / "target")] if role == "repair" else [])
                result = subprocess.run(args, cwd=root, env=env, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("gpt-6-astra", json.loads((root / "capture.json").read_text()))
                if role == "interpretation":
                    self.assertEqual(result.returncode, 78)


if __name__ == "__main__":
    unittest.main()
