import json
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = "/home/user/kg-autonomous"
SRC_RUNNER = os.path.join(ROOT, "evolution_eval", "kernel_runner.sh")
SRC_ORCHESTRATOR = os.path.join(ROOT, "evolution_eval", "kernel_orchestrator.py")


class KernelRunnerIntegrationTests(unittest.TestCase):
    def _write_common_files(self, case_dir: str, reason_code: str, first_exit: int, second_exit: int):
        eval_dir = os.path.join(case_dir, "evolution_eval")
        os.makedirs(eval_dir, exist_ok=True)
        shutil.copy2(SRC_RUNNER, os.path.join(eval_dir, "kernel_runner.sh"))
        shutil.copy2(SRC_ORCHESTRATOR, os.path.join(eval_dir, "kernel_orchestrator.py"))
        os.chmod(os.path.join(eval_dir, "kernel_runner.sh"), 0o755)

        with open(os.path.join(eval_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "num_runs": 1,
                    "workspace_cleanup": False,
                    "debug": False,
                    "output_dir": "evolution_eval/output",
                    "execution_command": "./mock_exec.sh",
                    "decision_limit_override": 1000000,
                    "failure_definition": {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"},
                    "logs": {"kernel": "kernel_runs.jsonl"},
                    "failure_injection": {
                        "enabled": False,
                        "mode": "dependency_error",
                        "rate": 0.0,
                        "seed": 1,
                        "kernel_enabled": False,
                    },
                },
                f,
            )

        with open(os.path.join(eval_dir, "clean_workspace.sh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(eval_dir, "clean_workspace.sh"), 0o755)

        with open(os.path.join(eval_dir, "repair_package_json.py"), "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "open('repair_called.flag','a',encoding='utf-8').write('1\\n')\n"
                "print('REPAIR_APPLIED')\n"
            )
        os.chmod(os.path.join(eval_dir, "repair_package_json.py"), 0o755)

        # Mock run_logger: classify drives strict repair trigger.
        with open(os.path.join(eval_dir, "run_logger.py"), "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import argparse, json, os\n"
                f"REASON='{reason_code}'\n"
                "def classify(artifact, decision_limit_reason_code):\n"
                "    if int(artifact.get('exit_code',1)) == 0:\n"
                "        return ('SUCCESS', None, False)\n"
                "    return ('FAIL', REASON, False)\n"
                "def main():\n"
                "    p=argparse.ArgumentParser()\n"
                "    p.add_argument('--log-file', required=True)\n"
                "    p.add_argument('--run-id', required=True)\n"
                "    p.add_argument('--artifact-file', required=True)\n"
                "    p.add_argument('--decision-limit-reason-code', required=True)\n"
                "    a=p.parse_args()\n"
                "    artifact=json.load(open(a.artifact_file, encoding='utf-8'))\n"
                "    st, rc, hb = classify(artifact, a.decision_limit_reason_code)\n"
                "    rec={\n"
                "      'run_id': a.run_id,\n"
                "      'status': st,\n"
                "      'reason_code': rc,\n"
                "      'hard_block': hb,\n"
                "      'repair_attempted': bool(artifact.get('repair_attempted', False)),\n"
                "      'repair_success': bool(artifact.get('repair_success', False)),\n"
                "      'exit_code': int(artifact.get('exit_code', 1))\n"
                "    }\n"
                "    os.makedirs(os.path.dirname(a.log_file), exist_ok=True)\n"
                "    with open(a.log_file, 'a', encoding='utf-8') as fw:\n"
                "        fw.write(json.dumps(rec)+'\\n')\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
        os.chmod(os.path.join(eval_dir, "run_logger.py"), 0o755)

        with open(os.path.join(case_dir, "mock_exec.sh"), "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/sh\n"
                "COUNT_FILE=mock_exec_count.txt\n"
                "c=0\n"
                "if [ -f \"$COUNT_FILE\" ]; then c=$(cat \"$COUNT_FILE\"); fi\n"
                "c=$((c+1))\n"
                "echo \"$c\" > \"$COUNT_FILE\"\n"
                f"if [ \"$c\" -eq 1 ]; then exit {first_exit}; fi\n"
                f"exit {second_exit}\n"
            )
        os.chmod(os.path.join(case_dir, "mock_exec.sh"), 0o755)

        os.makedirs(os.path.join(case_dir, "runtime", "mock_project", "core"), exist_ok=True)
        with open(os.path.join(case_dir, "runtime", ".last_generated_project"), "w", encoding="utf-8") as f:
            f.write("runtime/mock_project\n")
        with open(os.path.join(case_dir, "runtime", "mock_project", "core", "package.json"), "w", encoding="utf-8") as f:
            f.write('{"name":"x"}\n')

        return eval_dir

    def _run_case(self, reason_code: str, first_exit: int, second_exit: int):
        with tempfile.TemporaryDirectory(prefix="kernel-runner-it-") as td:
            eval_dir = self._write_common_files(td, reason_code, first_exit, second_exit)
            subprocess.check_call(["bash", os.path.join(eval_dir, "kernel_runner.sh")], cwd=td)

            artifact = os.path.join(td, "evolution_eval", "output", "run_artifacts", "run_kernel-001.json")
            with open(artifact, "r", encoding="utf-8") as f:
                data = json.load(f)

            return td, data

    def test_repair_triggered_for_dependency_error(self):
        td, artifact = self._run_case("DEPENDENCY_ERROR", first_exit=1, second_exit=0)
        self.assertTrue(artifact.get("repair_attempted"))
        self.assertTrue(artifact.get("repair_success"))
        self.assertEqual(int(artifact.get("exit_code")), 0)

    def test_repair_not_triggered_for_other_error(self):
        td, artifact = self._run_case("OTHER_ERROR", first_exit=1, second_exit=0)
        self.assertFalse(artifact.get("repair_attempted"))
        self.assertFalse(artifact.get("repair_success"))
        self.assertEqual(int(artifact.get("exit_code")), 1)


if __name__ == "__main__":
    unittest.main()
