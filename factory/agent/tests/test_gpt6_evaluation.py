"""Offline tests; the fake executable is not evidence of model access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "factory/agent"))
import gpt6_evaluation as evaluation


def campaign(commit: str) -> dict:
    return {"schema_version": evaluation.SCHEMA, "baseline_model": "gpt-5.3-codex",
            "candidate_model": "gpt-6-astra", "effort": "high", "budget_id": "frozen-budget",
            "source_commit": commit,
            "cases": [{"id": f"case-{i}",
                       "category": sorted(evaluation.CATEGORIES)[i % len(evaluation.CATEGORIES)],
                       "prompt": f"Inspect fixture case {i} without changing files."} for i in range(30)]}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "fixture.txt").write_text("frozen\n")
        subprocess.run(["git", "add", "fixture.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                                     check=True, capture_output=True, text=True).stdout.strip()
        self.campaign = campaign(self.commit)
        self.campaign_path = self.root / "campaign.json"
        self.campaign_path.write_text(json.dumps(self.campaign))
        self.fake = self.root / "codex"
        self.fake.write_text("""#!/usr/bin/env python3
import json,sys
if sys.argv[1:] == ['--version']:
 print('codex-cli 9.9.9-test'); raise SystemExit
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

    def test_dirty_or_wrong_workspace_is_rejected(self):
        (self.root / "fixture.txt").write_text("changed\n")
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.verify_workspace(self.root, self.commit)

    def test_probe_records_requested_not_verified_model(self):
        result = evaluation.probe("high", self.root, 10, str(self.fake))
        self.assertTrue(result["requested_model_call_completed"])
        self.assertEqual(result["requested_model"], "gpt-6-astra")
        self.assertFalse(result["provider_model_identity_verified"])

    def test_collect_uses_frozen_pair_and_private_files(self):
        evidence = self.root / "evidence"
        result = evaluation.collect(self.campaign_path, "case-0", "candidate", self.root,
                                    evidence, 10, str(self.fake))
        self.assertEqual(result["requested_model"], "gpt-6-astra")
        self.assertEqual(result["source_commit"], self.commit)
        self.assertEqual(os.stat(evidence).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(evidence / "case-0.candidate.receipt.json").st_mode & 0o777, 0o600)

    def test_compile_requires_separate_complete_grades(self):
        evidence = self.root / "evidence"
        grades = []
        for case in self.campaign["cases"]:
            for side in ("baseline", "candidate"):
                result = evaluation.collect(self.campaign_path, case["id"], side, self.root,
                                            evidence, 10, str(self.fake))
                grades.append({"case_id": case["id"], "side": side, "safety_pass": True,
                               "correctness": 1.0, "evidence_coverage": 1.0, "cost": 0.0,
                               "evaluator_ref": f"fixture-grade:{case['id']}:{side}"})
        grade_path = self.root / "grades.json"
        grade_path.write_text(json.dumps({"schema_version": "gpt6-evaluation-grades.v1",
                                          "campaign_sha256": evaluation.kernel.digest(self.campaign),
                                          "grades": grades}))
        output = evaluation.compile_report(self.campaign_path, evidence, grade_path)
        self.assertEqual(output["gate"]["paired_cases"], 30)
        self.assertFalse(output["gate"]["activated"])
        self.assertFalse(output["gate"]["provider_authenticity_verified"])
        self.assertIn("no_10_percent_operational_improvement", output["gate"]["reasons"])
        grades.pop()
        grade_path.write_text(json.dumps({"schema_version": "gpt6-evaluation-grades.v1",
                                          "campaign_sha256": evaluation.kernel.digest(self.campaign),
                                          "grades": grades}))
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.compile_report(self.campaign_path, evidence, grade_path)

    def test_compile_rejects_corrupted_raw_evidence(self):
        evidence = self.root / "evidence"
        grades = []
        for case in self.campaign["cases"]:
            for side in ("baseline", "candidate"):
                evaluation.collect(self.campaign_path, case["id"], side, self.root,
                                   evidence, 10, str(self.fake))
                grades.append({"case_id": case["id"], "side": side, "safety_pass": True,
                               "correctness": 1.0, "evidence_coverage": 1.0, "cost": 0.0,
                               "evaluator_ref": f"fixture-grade:{case['id']}:{side}"})
        (evidence / "case-0.candidate.jsonl").write_text('{"type":"error"}\n')
        grade_path = self.root / "grades-corrupt.json"
        grade_path.write_text(json.dumps({"schema_version": "gpt6-evaluation-grades.v1",
                                          "campaign_sha256": evaluation.kernel.digest(self.campaign),
                                          "grades": grades}))
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation.compile_report(self.campaign_path, evidence, grade_path)

    def test_malformed_event_stream_is_rejected(self):
        with self.assertRaises(evaluation.kernel.Rejected):
            evaluation._parse_events(b'{"type":"thread.started","thread_id":"x"}\n')


if __name__ == "__main__":
    unittest.main()
