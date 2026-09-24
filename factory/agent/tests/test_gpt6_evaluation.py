"""Offline tests; the fake executable is not evidence of model access."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "factory/agent"))
import gpt6_evaluation as evaluation


def campaign(commit: str) -> dict:
    cases = [{"id": f"case-{i}", "first_side": "baseline",
              "category": sorted(evaluation.CATEGORIES)[i % len(evaluation.CATEGORIES)],
              "prompt": f"Inspect fixture case {i} without changing files."} for i in range(30)]
    schedule = evaluation._counterbalanced_first_sides(commit, cases)
    for case in cases:
        case["first_side"] = schedule[case["id"]]
    return {"schema_version": evaluation.SCHEMA, "baseline_model": "gpt-5.3-codex",
            "candidate_model": "gpt-6-astra", "effort": "high", "budget_id": "frozen-budget",
            "timeout_seconds": 10, "max_pair_gap_seconds": 3600,
            "source_commit": commit, "cases": cases}


def bound_grade(sample: dict) -> dict:
    return {"sample_id": sample["sample_id"], "safety_pass": True,
            "correctness": 1.0, "evidence_coverage": 1.0,
            "evaluator_ref": f"fixture-grade:{sample['sample_id']}",
            "receipt_sha256": sample["receipt_sha256"],
            "event_stream_sha256": sample["event_stream_sha256"]}


def prepare_grades(root: Path, campaign_path: Path, evidence: Path,
                   suffix: str = "") -> tuple[Path, Path, Path, list[dict], dict]:
    manifest_path = root / f"blind{suffix}.json"
    mapping_path = root / f"blind-map{suffix}.json"
    evaluation.prepare_blind_grading(campaign_path, evidence, manifest_path, mapping_path)
    manifest = evaluation.kernel.load_json(manifest_path)
    mapping = evaluation.kernel.load_json(mapping_path)
    grades = [bound_grade(sample) for sample in mapping["samples"]]
    grade_path = root / f"grades{suffix}.json"
    grade_path.write_text(json.dumps({"schema_version": evaluation.GRADE_SCHEMA,
                                      "campaign_sha256": mapping["campaign_sha256"],
                                      "blind_manifest_sha256": mapping["blind_manifest_sha256"],
                                      "grades": grades}))
    return manifest_path, mapping_path, grade_path, grades, manifest


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.workspace, check=True)
        (self.workspace / "fixture.txt").write_text("frozen\n")
        (self.workspace / ".gitignore").write_text("ignored.txt\n")
        subprocess.run(["git", "add", "fixture.txt", ".gitignore"], cwd=self.workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.workspace, check=True)
        self.commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace,
                                     check=True, capture_output=True, text=True).stdout.strip()
        self.campaign = campaign(self.commit)
        self.candidate_case_id = next(case["id"] for case in self.campaign["cases"]
                                      if case["first_side"] == "candidate")
        self.baseline_case_id = next(case["id"] for case in self.campaign["cases"]
                                     if case["first_side"] == "baseline")
        self.campaign_path = self.root / "campaign.json"
        self.campaign_path.write_text(json.dumps(self.campaign))
        self.fake = self.root / "codex"
        self.fake.write_text("""#!/usr/bin/env python3
import json,os,sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9'); raise SystemExit
if sys.argv[1:] == ['login','status']:
 print('Logged in using ChatGPT'); raise SystemExit
if not os.isatty(0):
 print('Codex received non-terminal stdin', file=sys.stderr); raise SystemExit(98)
print(json.dumps({'type':'thread.started','thread_id':'test-thread'}))
print(json.dumps({'type':'turn.started'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'GPT6_ACCESS_PROBE_OK'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':0,'output_tokens':5,'reasoning_output_tokens':1}}))
""")
        self.fake.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def test_campaign_validation_and_required_categories(self):
        loaded = evaluation.load_campaign(self.campaign_path)
        self.assertEqual(len(loaded["cases"]), 30)
        bad = dict(self.campaign, cases=self.campaign["cases"][:-1])
        path = self.root / "bad.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.load_campaign(path)
        for timeout in (True, 0, 3601):
            with self.subTest(timeout=timeout):
                path.write_text(json.dumps(dict(self.campaign, timeout_seconds=timeout)))
                with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                            "timeout_seconds must be"):
                    evaluation.load_campaign(path)
        for pair_gap in (True, 0, 21_601):
            with self.subTest(pair_gap=pair_gap):
                path.write_text(json.dumps(dict(self.campaign,
                                                max_pair_gap_seconds=pair_gap)))
                with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                            "max_pair_gap_seconds must be"):
                    evaluation.load_campaign(path)
        unbalanced = json.loads(json.dumps(self.campaign))
        for case in unbalanced["cases"]:
            case["first_side"] = "baseline"
        path.write_text(json.dumps(unbalanced))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "execution order must be counterbalanced"):
            evaluation.load_campaign(path)
        selected = json.loads(json.dumps(self.campaign))
        baseline = next(case for case in selected["cases"] if case["first_side"] == "baseline")
        candidate = next(case for case in selected["cases"] if case["first_side"] == "candidate")
        baseline["first_side"], candidate["first_side"] = candidate["first_side"], baseline["first_side"]
        path.write_text(json.dumps(selected))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "deterministic campaign schedule"):
            evaluation.load_campaign(path)

    def test_dirty_or_wrong_workspace_is_rejected(self):
        (self.workspace / "fixture.txt").write_text("changed\n")
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.verify_workspace(self.workspace, self.commit)

    def test_untracked_workspace_input_is_rejected(self):
        (self.workspace / "untracked.txt").write_text("not frozen\n")
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.verify_workspace(self.workspace, self.commit)

    def test_ignored_workspace_input_is_rejected(self):
        (self.workspace / "ignored.txt").write_text("not represented by the commit\n")
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.verify_workspace(self.workspace, self.commit)

    def test_probe_records_requested_not_verified_model(self):
        result = evaluation.probe("high", self.workspace, 10, str(self.fake))
        self.assertTrue(result["requested_model_call_completed"])
        self.assertEqual(result["requested_model"], "gpt-6-astra")
        self.assertEqual(result["auth_surface"], "chatgpt")
        self.assertFalse(result["provider_model_identity_verified"])

    def test_auth_surface_classifier_never_returns_status_payload(self):
        secret = b"Logged in as private@example.invalid with token sk-private"
        completed = subprocess.CompletedProcess([], 0, secret, b"")
        with mock.patch.object(evaluation.subprocess, "run", return_value=completed):
            result = evaluation._codex_auth_surface(str(self.fake))
        self.assertEqual(result, "unknown")
        self.assertNotIn("private", result)

    def test_auth_surface_classifier_distinguishes_billing_scope(self):
        for output, expected in ((b"Logged in using ChatGPT", "chatgpt"),
                                 (b"Logged in using API key", "api_key"),
                                 (b"Logged in using personal access token", "access_token")):
            completed = subprocess.CompletedProcess([], 0, output, b"")
            with self.subTest(expected=expected), \
                    mock.patch.object(evaluation.subprocess, "run", return_value=completed):
                self.assertEqual(evaluation._codex_auth_surface(str(self.fake)), expected)

    def test_probe_rejects_unknown_auth_before_model_call(self):
        with mock.patch.object(evaluation, "_codex_auth_surface", return_value="unknown"), \
                mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                        "recognized Codex authentication"):
                evaluation.probe("high", self.workspace, 10, str(self.fake))
        execute.assert_not_called()

    def test_probe_rejects_old_cli_before_model_call(self):
        old = self.root / "old-codex"
        old.write_text("""#!/usr/bin/env python3
import sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 0.151.0'); raise SystemExit
raise SystemExit(99)
""")
        old.chmod(0o755)
        with self.assertRaises(evaluation.IncompatibleCodexCli):
            evaluation.probe("high", self.workspace, 10, str(old))

    def test_probe_uses_terminal_stdin_for_noninteractive_codex(self):
        with mock.patch.object(evaluation.subprocess, "run",
                               wraps=evaluation.subprocess.run) as run:
            evaluation.probe("high", self.workspace, 10, str(self.fake))
        self.assertEqual(len(run.call_args_list), 3)
        self.assertIs(run.call_args_list[0].kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(run.call_args_list[1].kwargs["stdin"], subprocess.DEVNULL)
        self.assertIsInstance(run.call_args_list[2].kwargs["stdin"], int)

    def test_collect_uses_frozen_pair_and_private_files(self):
        evidence = self.root / "evidence"
        result = evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                    evidence, 10, str(self.fake))
        self.assertEqual(result["requested_model"], "gpt-6-astra")
        self.assertEqual(result["source_commit"], self.commit)
        self.assertEqual(os.stat(evidence).st_mode & 0o777, 0o700)
        receipt_path = evidence / f"{self.candidate_case_id}.candidate.receipt.json"
        self.assertEqual(os.stat(receipt_path).st_mode & 0o777, 0o600)

    def test_collect_rejects_timeout_mismatch_before_model_call(self):
        with mock.patch.object(evaluation, "_codex_version") as version, \
                mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                        "timeout does not match campaign"):
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   self.root / "evidence", 9, str(self.fake))
        version.assert_not_called()
        execute.assert_not_called()

    def test_collect_rejects_second_side_before_campaign_first_side(self):
        with mock.patch.object(evaluation, "_codex_version") as version, \
                mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                        "first side must complete"):
                evaluation.collect(self.campaign_path, self.baseline_case_id, "candidate", self.workspace,
                                   self.root / "evidence", 10, str(self.fake))
        version.assert_not_called()
        execute.assert_not_called()

    def test_ignored_workspace_drift_during_execution_is_not_promoted(self):
        evidence = self.root / "evidence"
        real_execute = evaluation.execute

        def execute_then_mutate(*args, **kwargs):
            result = real_execute(*args, **kwargs)
            (self.workspace / "ignored.txt").write_text("created during execution\n")
            return result

        with mock.patch.object(evaluation, "execute", side_effect=execute_then_mutate):
            with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                        "workspace must be clean"):
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(self.fake))
        stem = self.candidate_case_id + ".candidate"
        self.assertTrue((evidence / ("." + stem + ".lock")).is_file())
        self.assertFalse((evidence / (stem + ".receipt.json")).exists())
        self.assertFalse((evidence / (stem + ".jsonl")).exists())

    def test_timeout_preserves_private_partial_evidence_without_counting_completion(self):
        slow = self.root / "slow-codex"
        slow.write_text("""#!/usr/bin/env python3
import json,sys,time
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9'); raise SystemExit
if sys.argv[1:] == ['login','status']:
 print('Logged in using ChatGPT'); raise SystemExit
print(json.dumps({'type':'thread.started','thread_id':'private-thread'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
time.sleep(5)
""")
        slow.chmod(0o755)
        self.campaign["timeout_seconds"] = 1
        self.campaign_path.write_text(json.dumps(self.campaign))
        evidence = self.root / "evidence"
        with self.assertRaises(evaluation.CodexRunBlocked) as raised:
            evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                               evidence, 1, str(slow))
        summary = raised.exception.summary
        self.assertFalse(summary["completed"])
        self.assertTrue(summary["thread_started_observed"])
        self.assertTrue(summary["turn_started_observed"])
        self.assertFalse(summary["turn_completed_observed"])
        self.assertNotIn("private-thread", json.dumps(summary))
        receipt = Path(raised.exception.receipt_path)
        raw = Path(raised.exception.raw_path)
        self.assertTrue(receipt.is_file())
        self.assertIn("private-thread", raw.read_text())
        self.assertEqual(receipt.parent.parent, evidence / "blocked")
        self.assertFalse((evidence / f".{self.candidate_case_id}.candidate.lock").exists())
        self.assertEqual(os.stat(receipt).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(raw).st_mode & 0o777, 0o600)

    def test_process_failure_preserves_events_but_not_stderr_payload(self):
        failed = self.root / "failed-codex"
        failed.write_text("""#!/usr/bin/env python3
import json,sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9'); raise SystemExit
if sys.argv[1:] == ['login','status']:
 print('Logged in using ChatGPT'); raise SystemExit
print(json.dumps({'type':'thread.started','thread_id':'private-thread'}))
print(json.dumps({'type':'error','message':'private-model-error'}))
print('private-stderr-detail', file=sys.stderr)
raise SystemExit(23)
""")
        failed.chmod(0o755)
        evidence = self.root / "evidence"
        with self.assertRaises(evaluation.CodexRunBlocked) as raised:
            evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                               evidence, 10, str(failed))
        summary = raised.exception.summary
        self.assertEqual(summary["reason"], "process-failure")
        self.assertEqual(summary["process_returncode"], 23)
        self.assertTrue(summary["failure_event_observed"])
        self.assertFalse(summary["completed"])
        self.assertNotIn("private-model-error", json.dumps(summary))
        self.assertNotIn("private-stderr-detail", json.dumps(summary))
        raw = Path(raised.exception.raw_path)
        stderr = Path(raised.exception.stderr_path)
        self.assertIn("private-model-error", raw.read_text())
        self.assertIn("private-stderr-detail", stderr.read_text())
        self.assertEqual(os.stat(stderr).st_mode & 0o777, 0o600)

    def test_invalid_success_stream_is_blocked_with_safe_diagnostics(self):
        malformed = self.root / "malformed-codex"
        malformed.write_text("#!/bin/sh\nprintf 'not-json\\n'\n")
        malformed.chmod(0o755)
        with self.assertRaises(evaluation.CodexRunBlocked) as raised:
            evaluation.execute("gpt-6-astra", "high", "test", self.workspace, 10,
                               str(malformed))
        summary = raised.exception.summary
        self.assertEqual(summary["reason"], "invalid-or-incomplete-event-stream")
        self.assertEqual(summary["process_returncode"], 0)
        self.assertEqual(summary["malformed_event_line_count"], 1)

    def test_incomplete_success_stream_preserves_private_evidence(self):
        incomplete = self.root / "incomplete-codex"
        incomplete.write_text("""#!/usr/bin/env python3
import json,sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9'); raise SystemExit
if sys.argv[1:] == ['login','status']:
 print('Logged in using ChatGPT'); raise SystemExit
print(json.dumps({'type':'thread.started','thread_id':'private-thread'}))
print(json.dumps({'type':'turn.started'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100}}))
""")
        incomplete.chmod(0o755)
        evidence = self.root / "evidence"
        with self.assertRaises(evaluation.CodexRunBlocked) as raised:
            evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                               evidence, 10, str(incomplete))
        summary = raised.exception.summary
        self.assertEqual(summary["reason"], "invalid-or-incomplete-event-stream")
        self.assertTrue(summary["turn_completed_observed"])
        self.assertFalse(summary["completed"])
        self.assertNotIn("private-thread", json.dumps(summary))
        raw = Path(raised.exception.raw_path)
        self.assertIn("private-thread", raw.read_text())
        self.assertEqual(os.stat(raw).st_mode & 0o777, 0o600)

    def test_completed_evidence_is_never_overwritten_or_reexecuted(self):
        evidence = self.root / "evidence"
        first = evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(self.fake))
        before = Path(first["receipt_path"]).read_bytes()
        with mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaises(evaluation.kernel.Rejected):
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(self.fake))
        execute.assert_not_called()
        self.assertEqual(Path(first["receipt_path"]).read_bytes(), before)

    def test_concurrent_or_uncertain_attempt_is_not_reissued(self):
        evidence = self.root / "evidence"
        evidence.mkdir(mode=0o700)
        lock = evidence / f".{self.candidate_case_id}.candidate.lock"
        lock.write_text("existing uncertain attempt\n")
        with mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaises(evaluation.kernel.Rejected):
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(self.fake))
        execute.assert_not_called()
        self.assertEqual(lock.read_text(), "existing uncertain attempt\n")

    def test_symlinked_evidence_directory_is_rejected_before_inference(self):
        real = self.root / "real-evidence"
        real.mkdir()
        evidence = self.root / "evidence"
        evidence.symlink_to(real, target_is_directory=True)
        with mock.patch.object(evaluation, "execute") as execute:
            with self.assertRaises(evaluation.kernel.Rejected):
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(self.fake))
        execute.assert_not_called()
        self.assertEqual(list(real.iterdir()), [])

    def test_blocked_retries_keep_each_private_attempt(self):
        failed = self.root / "blocked-codex"
        failed.write_text("""#!/usr/bin/env python3
import json,sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9'); raise SystemExit
if sys.argv[1:] == ['login','status']:
 print('Logged in using ChatGPT'); raise SystemExit
print(json.dumps({'type':'error','message':'private'}))
raise SystemExit(23)
""")
        failed.chmod(0o755)
        evidence = self.root / "evidence"
        receipts = []
        for _ in range(2):
            with self.assertRaises(evaluation.CodexRunBlocked) as raised:
                evaluation.collect(self.campaign_path, self.candidate_case_id, "candidate", self.workspace,
                                   evidence, 10, str(failed))
            receipts.append(Path(raised.exception.receipt_path))
        self.assertNotEqual(receipts[0].parent, receipts[1].parent)
        self.assertTrue(all(path.is_file() for path in receipts))
        self.assertFalse((evidence / f".{self.candidate_case_id}.candidate.lock").exists())

    def test_completion_without_thread_id_is_blocked(self):
        events = b"\n".join((
            b'{"type":"thread.started"}',
            b'{"type":"item.completed","item":{"type":"agent_message","text":"private"}}',
            b'{"type":"turn.completed","usage":{"input_tokens":1}}',
        )) + b"\n"
        completed = subprocess.CompletedProcess([], 0, events, b"")
        with mock.patch.object(evaluation.subprocess, "run", return_value=completed):
            with self.assertRaises(evaluation.CodexRunBlocked) as raised:
                evaluation.execute("gpt-6-astra", "high", "test", self.workspace, 10,
                                   str(self.fake))
        self.assertEqual(raised.exception.summary["reason"],
                         "invalid-or-incomplete-event-stream")
        self.assertNotIn("private", json.dumps(raised.exception.summary))

    def test_probe_cli_writes_blocked_receipt_and_safe_stdout(self):
        partial = b'{"type":"thread.started","thread_id":"private-thread"}\n'
        summary = {
            "schema_version": evaluation.BLOCKED_SCHEMA,
            "status": "blocked",
            "reason": "timeout",
            "requested_model": "gpt-6-astra",
            "requested_effort": "high",
            "timeout_seconds": 1,
            "latency_ms": 1000.0,
            "process_returncode": None,
            "stdout_bytes": len(partial),
            "stdout_sha256": evaluation._sha_bytes(partial),
            "stderr_bytes": 0,
            "stderr_sha256": evaluation._sha_bytes(b""),
            **evaluation._partial_event_summary(partial),
        }
        blocked = evaluation.CodexRunBlocked(summary, partial, b"")
        output = self.root / "probe.json"
        stdout = io.StringIO()
        argv = ["gpt6_evaluation.py", "probe", "--effort", "high",
                "--timeout-seconds", "1", "--output", str(output)]
        with mock.patch.object(evaluation, "probe", side_effect=blocked), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            self.assertEqual(evaluation.main(), 2)
        public = json.loads(stdout.getvalue())
        self.assertFalse(public["requested_model_call_completed"])
        self.assertNotIn("private-thread", stdout.getvalue())
        self.assertIn("private-thread", output.with_name("probe.blocked.jsonl").read_text())
        self.assertEqual(output.with_name("probe.blocked.stderr").read_bytes(), b"")
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_compile_requires_separate_complete_grades(self):
        evidence = self.root / "evidence"
        for case in self.campaign["cases"]:
            second_side = "candidate" if case["first_side"] == "baseline" else "baseline"
            for side in (case["first_side"], second_side):
                evaluation.collect(self.campaign_path, case["id"], side, self.workspace,
                                   evidence, 10, str(self.fake))
        manifest_path, mapping_path, grade_path, grades, manifest = prepare_grades(
            self.root, self.campaign_path, evidence)
        manifest_text = manifest_path.read_text()
        for forbidden in ('"side"', '"requested_model"', '"pair_order"',
                          '"requested_effort"', '"latency_ms"', '"auth_surface"'):
            self.assertNotIn(forbidden, manifest_text)
        self.assertEqual(len(manifest["samples"]), 60)
        output = evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                           manifest_path, mapping_path)
        self.assertEqual(output["gate"]["paired_cases"], 30)
        self.assertFalse(output["gate"]["activated"])
        self.assertFalse(output["gate"]["provider_authenticity_verified"])
        self.assertIn("no_10_percent_operational_improvement", output["gate"]["reasons"])
        self.assertEqual(output["comparison_conditions"]["codex_version"], "codex-cli 9.9.9")
        self.assertEqual(output["comparison_conditions"]["auth_surface"], "chatgpt")
        self.assertEqual(output["comparison_conditions"]["blind_manifest_sha256"],
                         evaluation.kernel.digest(manifest))
        self.assertTrue(output["comparison_conditions"]["independent_grading_blinded"])
        self.assertEqual(output["comparison_conditions"]["cost_metric_source"],
                         "unavailable_not_evaluator_supplied")
        self.assertEqual(output["gate"]["totals"]["baseline"]["cost"], 0.0)
        self.assertEqual(output["gate"]["totals"]["candidate"]["cost"], 0.0)
        self.assertNotIn("cost", output["gate"]["improved_metrics"])
        self.assertEqual(output["gate"]["totals"]["baseline"]["total_tokens"], 3150.0)
        self.assertEqual(output["gate"]["totals"]["candidate"]["total_tokens"], 3150.0)
        self.assertNotIn("input_tokens", output["gate"]["totals"]["baseline"])
        self.assertEqual(output["comparison_conditions"]["token_metric_source"],
                         "event_input_plus_output_tokens")
        self.assertEqual(output["comparison_conditions"]["timeout_seconds"], 10)
        self.assertEqual(output["comparison_conditions"]["max_pair_gap_seconds"], 3600)
        self.assertLessEqual(output["comparison_conditions"]["max_observed_pair_gap_seconds"], 2)
        self.assertEqual(output["comparison_conditions"]["baseline_first_pairs"], 15)
        self.assertEqual(output["comparison_conditions"]["candidate_first_pairs"], 15)
        self.assertEqual(os.stat(manifest_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(mapping_path).st_mode & 0o777, 0o600)
        alt_manifest_path, _, _, _, alt_manifest = prepare_grades(
            self.root, self.campaign_path, evidence, "-alt")
        self.assertTrue({sample["sample_id"] for sample in manifest["samples"]}.isdisjoint(
            {sample["sample_id"] for sample in alt_manifest["samples"]}))
        self.assertNotEqual(evaluation.kernel.digest(manifest),
                            evaluation.kernel.digest(alt_manifest))
        self.assertEqual(os.stat(alt_manifest_path).st_mode & 0o777, 0o600)
        grade_doc = evaluation.kernel.load_json(grade_path)
        grade_doc["grades"][0]["cost"] = 0.0
        grade_path.write_text(json.dumps(grade_doc))
        with self.assertRaisesRegex(evaluation.kernel.Rejected, "invalid grade"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        grade_doc["grades"][0].pop("cost")
        grade_path.write_text(json.dumps(grade_doc))
        manifest_doc = evaluation.kernel.load_json(manifest_path)
        manifest_doc["samples"][0]["final_message"] += " tampered"
        manifest_path.write_text(json.dumps(manifest_doc))
        with self.assertRaisesRegex(evaluation.kernel.Rejected, "invalid blind mapping"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(evaluation.kernel.Rejected, "must be new distinct"):
            evaluation.prepare_blind_grading(self.campaign_path, evidence,
                                             manifest_path, mapping_path)
        receipt_path = evidence / f"{self.candidate_case_id}.candidate.receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["auth_surface"] = "api_key"
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "authentication surface changed"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["auth_surface"] = "chatgpt"
        receipt_path.write_text(json.dumps(receipt))
        receipt["codex_version"] = "codex-cli 0.152.0"
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "Codex CLI version is incompatible"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["codex_version"] = "codex-cli 9.9.8"
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "Codex CLI version changed"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["codex_version"] = "codex-cli 9.9.9"
        receipt_path.write_text(json.dumps(receipt))
        receipt["timeout_seconds"] = 9
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "receipt does not match campaign"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["timeout_seconds"] = 10
        receipt_path.write_text(json.dumps(receipt))
        original_observed_at = receipt["observed_at"]
        receipt["observed_at"] = "2099-01-01T00:00:00Z"
        receipt_path.write_text(json.dumps(receipt))
        gap_manifest, gap_mapping, gap_grades_path, _, _ = prepare_grades(
            self.root, self.campaign_path, evidence, "-gap")
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "paired observations exceeded"):
            evaluation.compile_report(self.campaign_path, evidence, gap_grades_path,
                                      gap_manifest, gap_mapping)
        receipt["observed_at"] = original_observed_at
        receipt_path.write_text(json.dumps(receipt))
        original_latency = receipt["latency_ms"]
        receipt["latency_ms"] = 0
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "invalid completion metrics"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["latency_ms"] = original_latency
        receipt_path.write_text(json.dumps(receipt))
        # Binding is checked separately from fields that can be re-derived from JSONL.
        original_input_tokens = receipt["input_tokens"]
        receipt["input_tokens"] += 1
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "completion metrics do not match raw evidence"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["input_tokens"] = original_input_tokens
        receipt_path.write_text(json.dumps(receipt))
        receipt["latency_ms"] += 1
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "blind manifest or mapping does not match evidence"):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)
        receipt["latency_ms"] -= 1
        receipt_path.write_text(json.dumps(receipt))
        grades.pop()
        grade_doc = evaluation.kernel.load_json(grade_path)
        grade_doc["grades"] = grades
        grade_path.write_text(json.dumps(grade_doc))
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)

    def test_compile_rejects_corrupted_raw_evidence(self):
        evidence = self.root / "evidence"
        for case in self.campaign["cases"]:
            second_side = "candidate" if case["first_side"] == "baseline" else "baseline"
            for side in (case["first_side"], second_side):
                evaluation.collect(self.campaign_path, case["id"], side, self.workspace,
                                   evidence, 10, str(self.fake))
        manifest_path, mapping_path, grade_path, _, _ = prepare_grades(
            self.root, self.campaign_path, evidence, "-corrupt")
        (evidence / f"{self.candidate_case_id}.candidate.jsonl").write_text('{"type":"error"}\n')
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.compile_report(self.campaign_path, evidence, grade_path,
                                      manifest_path, mapping_path)

    def test_malformed_event_stream_is_rejected(self):
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation._parse_events(b'{"type":"thread.started","thread_id":"x"}\n')

    def test_completion_requires_output_token_usage(self):
        events = b"\n".join((
            b'{"type":"thread.started","thread_id":"thread"}',
            b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            b'{"type":"turn.completed","usage":{"input_tokens":1}}',
        )) + b"\n"
        with self.assertRaisesRegex(evaluation.kernel.Rejected,
                                    "completion is missing usage"):
            evaluation._completion_evidence(events)


if __name__ == "__main__":
    unittest.main()
