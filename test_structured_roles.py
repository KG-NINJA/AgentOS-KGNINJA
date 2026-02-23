import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
QUESTION_LINE = "QUESTION: Does the queue markdown still assume docs/ARCHITECTURE.md is pre-existing?"
MOST_FRAGILE_LINE = "MOST_FRAGILE_POINT: docs/ARCHITECTURE.md is an unverified dependency"
NEXT_CHANGE_LINE = "NEXT_CHANGE: Inspect docs/ARCHITECTURE.md to validate plan"


class StructuredRolesTest(unittest.TestCase):
    def run_pipeline_once(self, base: Path) -> dict:
        runtime_dir = base / "runtime"
        workspace_dir = base / "workspace"
        memory_dir = base / "memory"
        queue_dir = base / "queue"

        for path in (runtime_dir, workspace_dir, memory_dir, queue_dir):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)

        queue_file = queue_dir / "idea.md"
        queue_file.write_text(
            """# Demo Project\n\n- deterministic\n- minimal\nBuild a sample scaffold.""",
            encoding="utf-8",
        )

        intent_spec = runtime_dir / "intent.yaml"
        architecture = runtime_dir / "architecture.md"
        project_dir = workspace_dir / "project-001"
        critique = runtime_dir / "critique.md"
        memory_log = memory_dir / "log-20260216.md"
        structured_log = runtime_dir / "structured_log.txt"

        env = os.environ.copy()
        env.update(
            {
                "STRUCTURED_RUNTIME_DIR": str(runtime_dir),
                "STRUCTURED_WORKSPACE_DIR": str(workspace_dir),
                "STRUCTURED_MEMORY_DIR": str(memory_dir),
                "STRUCTURED_LOG_FILE": str(structured_log),
                "STRUCTURED_SKIP_MEMORY_RECORD": "1",
                "STRUCTURED_FIXED_TIMESTAMP": "2026-02-16T00:00:00Z",
                "STRUCTURED_QUEUE_ROOT": str(base),
            }
        )

        subprocess.run(
            [str(REPO_ROOT / "factory/roles/planner.sh"), str(queue_file), str(intent_spec)],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )
        subprocess.run(
            [str(REPO_ROOT / "factory/roles/architect.sh"), str(intent_spec), str(architecture)],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )
        subprocess.run(
            [str(REPO_ROOT / "factory/roles/builder.sh"), str(architecture), str(project_dir)],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )
        subprocess.run(
            [str(REPO_ROOT / "factory/roles/critic.sh"), str(project_dir), str(critique)],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )
        subprocess.run(
            [str(REPO_ROOT / "factory/roles/reflector.sh"), str(critique), str(memory_log)],
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )

        project_snapshot = {}
        for file_path in project_dir.rglob('*'):
            if file_path.is_file():
                rel = file_path.relative_to(project_dir).as_posix()
                project_snapshot[rel] = file_path.read_text(encoding="utf-8")

        guardrail_file = project_dir / "STRUCTURED_GUARDRAILS.txt"
        data = {
            "intent": intent_spec.read_text(encoding="utf-8"),
            "architecture": architecture.read_text(encoding="utf-8"),
            "critique": critique.read_text(encoding="utf-8"),
            "memory": memory_log.read_text(encoding="utf-8"),
            "log": structured_log.read_text(encoding="utf-8"),
            "builder_guardrail": guardrail_file.read_text(encoding="utf-8"),
            "project": project_snapshot,
        }

        return data

    def test_structured_pipeline_outputs_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = self.run_pipeline_once(base)
            self.assertIn("core/main.py", first["project"])
            self.assertIn("docs/ARCHITECTURE.md", first["project"])
            self.assertIn("role=planner", first["log"])
            self.assertIn("role=architect", first["log"])
            self.assertIn("role=builder", first["log"])
            self.assertIn(QUESTION_LINE, first["intent"])
            self.assertIn(QUESTION_LINE, first["architecture"])
            self.assertIn(QUESTION_LINE, first["builder_guardrail"])
            self.assertIn(QUESTION_LINE, first["critique"])
            self.assertIn(QUESTION_LINE, first["memory"])
            self.assertIn(MOST_FRAGILE_LINE, first["critique"])
            self.assertIn(NEXT_CHANGE_LINE, first["memory"])
            self.assertTrue(first["intent"].strip())
            self.assertTrue(first["architecture"].strip())
            self.assertTrue(first["critique"].strip())
            self.assertTrue(first["memory"].strip())

            second = self.run_pipeline_once(base)
            self.assertEqual(first["intent"], second["intent"])
            self.assertEqual(first["architecture"], second["architecture"])
            self.assertEqual(first["project"], second["project"])
            self.assertEqual(first["critique"], second["critique"])
            self.assertEqual(first["memory"], second["memory"])
            self.assertEqual(first["log"], second["log"])


if __name__ == "__main__":
    unittest.main()
