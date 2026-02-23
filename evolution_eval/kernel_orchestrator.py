#!/usr/bin/env python3
"""Kernel orchestration in Python for deterministic, testable repair flow.

Design:
- `run_kernel_pipeline` is pure-orchestrator logic and accepts injected callables.
- CLI wiring handles shell execution, artifact capture, and JSONL append.
- Status normalization is strict and rejects unknown status strings.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


StatusReasonHard = Tuple[str, Optional[str], bool]
ExecutorFn = Callable[[], Dict[str, Any]]
ClassifierFn = Callable[[Dict[str, Any], str], StatusReasonHard]
RepairFn = Callable[[], bool]


@dataclass(frozen=True)
class KernelRuntimeConfig:
    root_dir: Path
    output_dir: Path
    log_file: Path
    artifact_dir: Path
    num_runs: int
    workspace_cleanup: bool
    debug: bool
    execution_command: str
    decision_limit_override: int
    decision_limit_reason_code: str
    clean_script: Path
    repair_script: Path
    run_logger_script: Path


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return "".join(lines[-n:])


def _extract_trace_fields(trace_tail: str) -> Tuple[str, str]:
    for raw in reversed(trace_tail.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        return str(data.get("stage", "")), str(data.get("reason", ""))
    return "", ""


def _parse_status_from_index(index_tail: str, marker: str) -> str:
    for line in reversed(index_tail.splitlines()):
        if marker in line:
            part = line.split(marker, 1)[1].strip()
            return part.split()[0] if part else ""
    return ""


def _normalize_status(status: str) -> str:
    normalized = str(status).strip().upper()
    # Backward-compatible canonicalization for legacy classifier outputs.
    if normalized == "FAILURE":
        normalized = "FAIL"
    if normalized not in {"SUCCESS", "FAIL"}:
        raise ValueError(f"invalid status from classifier: {status!r}")
    return normalized


def _default_classifier(artifact: Dict[str, Any], decision_limit_reason_code: str) -> StatusReasonHard:
    """Adapter around run_logger.classify with strict status normalization."""
    module_path = Path(__file__).resolve().parent / "run_logger.py"
    spec = importlib.util.spec_from_file_location("run_logger_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_logger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    status, reason_code, hard_block = mod.classify(artifact, decision_limit_reason_code)
    return status, reason_code, hard_block


def _build_shell_executor(
    *,
    root_dir: Path,
    execution_command: str,
    decision_limit_override: int,
    debug: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> ExecutorFn:
    def _executor() -> Dict[str, Any]:
        env = os.environ.copy()
        env["FACTORY_PROJECT_LIMIT"] = str(decision_limit_override)
        env["FACTORY_TRACE_ENABLE"] = "1" if debug else "0"

        with stdout_path.open("a", encoding="utf-8") as out_fh, stderr_path.open("a", encoding="utf-8") as err_fh:
            proc = subprocess.run(
                ["bash", "-lc", execution_command],
                cwd=str(root_dir),
                env=env,
                stdout=out_fh,
                stderr=err_fh,
                check=False,
            )

        index_tail = _tail(root_dir / "runtime" / "index.log", 20)
        trace_tail = _tail(root_dir / "runtime" / "factory_exit_trace.log", 20)
        stderr_tail = _tail(stderr_path, 20)
        stdout_tail = _tail(stdout_path, 20)
        decision_status = _parse_status_from_index(index_tail, "DECISION status=")
        post_gate_status = _parse_status_from_index(index_tail, "POST_GATE status=")
        trace_stage, trace_reason = _extract_trace_fields(trace_tail)

        return {
            "exit_code": int(proc.returncode),
            "decision_status": decision_status,
            "post_gate_status": post_gate_status,
            "stderr_tail": stderr_tail,
            "stdout_tail": stdout_tail,
            "trace_tail": trace_tail,
            "trace_stage": trace_stage,
            "trace_reason": trace_reason,
        }

    return _executor


def _build_repairer(root_dir: Path, repair_script: Path, stdout_path: Path, stderr_path: Path) -> RepairFn:
    def _repairer() -> bool:
        last_project_file = root_dir / "runtime" / ".last_generated_project"
        if not last_project_file.exists():
            return False
        project_dir = last_project_file.read_text(encoding="utf-8").strip()
        if not project_dir:
            return False

        project_path = Path(project_dir)
        if not project_path.is_absolute():
            project_path = root_dir / project_path
        if not project_path.exists():
            return False

        with stdout_path.open("a", encoding="utf-8") as out_fh, stderr_path.open("a", encoding="utf-8") as err_fh:
            proc = subprocess.run(
                [sys.executable, str(repair_script), str(project_path)],
                cwd=str(root_dir),
                stdout=out_fh,
                stderr=err_fh,
                check=False,
            )
        return proc.returncode == 0

    return _repairer


def _append_marker(root_dir: Path, key: str, value: str) -> None:
    activity_log = root_dir / "runtime" / "activity.log"
    activity_log.parent.mkdir(parents=True, exist_ok=True)
    with activity_log.open("a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def run_kernel_pipeline(
    config: Dict[str, Any],
    artifact_path: str,
    *,
    executor: Optional[ExecutorFn] = None,
    classifier: Optional[ClassifierFn] = None,
    repairer: Optional[RepairFn] = None,
) -> Dict[str, Any]:
    """Execute classify/repair/re-run pipeline and persist deterministic artifact JSON."""
    if executor is None:
        raise ValueError("executor is required")

    classifier_fn = classifier or _default_classifier
    repairer_fn = repairer or (lambda: False)

    decision_limit_reason_code = str(config["decision_limit_reason_code"])

    artifact = dict(executor())
    artifact.setdefault("injection_applied", bool(config.get("injection_applied", False)))
    artifact.setdefault("injection_mode", config.get("injection_mode"))
    artifact["repair_attempted"] = False
    artifact["repair_success"] = False

    raw_initial_status, initial_reason_code, initial_hard = classifier_fn(artifact, decision_limit_reason_code)
    initial_status = _normalize_status(raw_initial_status)

    repair_trigger = initial_status == "FAIL" and initial_reason_code == "DEPENDENCY_ERROR"
    final_status = initial_status
    final_reason_code = initial_reason_code
    final_hard = bool(initial_hard)

    if repair_trigger:
        artifact["repair_attempted"] = True
        if repairer_fn():
            rerun_artifact = dict(executor())
            for key, value in rerun_artifact.items():
                artifact[key] = value
            raw_final_status, final_reason_code, final_hard = classifier_fn(artifact, decision_limit_reason_code)
            final_status = _normalize_status(raw_final_status)
            artifact["repair_success"] = final_status == "SUCCESS"
        else:
            artifact["repair_success"] = False

    artifact["status"] = final_status
    artifact["reason_code"] = final_reason_code
    artifact["hard_block"] = final_hard

    _write_json(Path(artifact_path), artifact)

    return OrderedDict(
        [
            ("initial_status", initial_status),
            ("final_status", final_status),
            ("repair_attempted", bool(artifact["repair_attempted"])),
            ("repair_success", bool(artifact["repair_success"])),
            ("reason_code", final_reason_code),
            ("normalized", True),
        ]
    )


def _command_available(root_dir: Path, execution_command: str) -> bool:
    parts = shlex.split(execution_command)
    if not parts:
        return False
    cmd = parts[0]
    if cmd.startswith("./"):
        return (root_dir / cmd[2:]).exists()
    if cmd.startswith("/"):
        return Path(cmd).exists()
    return shutil.which(cmd) is not None


def _load_runtime_config(script_dir: Path, root_dir: Path) -> KernelRuntimeConfig:
    config_json = _read_json(script_dir / "config.json")
    output_dir = Path(config_json.get("output_dir", "evolution_eval/output"))
    if not output_dir.is_absolute():
        output_dir = root_dir / output_dir

    num_runs = int(os.environ.get("RUN_COUNT_OVERRIDE", config_json.get("num_runs", 5)))

    return KernelRuntimeConfig(
        root_dir=root_dir,
        output_dir=output_dir,
        log_file=output_dir / str(config_json["logs"]["kernel"]),
        artifact_dir=output_dir / "run_artifacts",
        num_runs=num_runs,
        workspace_cleanup=bool(config_json.get("workspace_cleanup", True)),
        debug=bool(config_json.get("debug", False)),
        execution_command=str(config_json.get("execution_command", "./factory.sh run")),
        decision_limit_override=int(config_json.get("decision_limit_override", 1000000)),
        decision_limit_reason_code=str(
            config_json.get("failure_definition", {}).get("decision_limit_reason_code", "DECISION_LIMIT_EXCLUDED")
        ),
        clean_script=script_dir / "clean_workspace.sh",
        repair_script=script_dir / "repair_package_json.py",
        run_logger_script=script_dir / "run_logger.py",
    )


def _invoke_run_logger(run_logger_script: Path, log_file: Path, run_id: str, artifact_file: Path, decision_limit_reason_code: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(run_logger_script),
            "--log-file",
            str(log_file),
            "--run-id",
            run_id,
            "--artifact-file",
            str(artifact_file),
            "--decision-limit-reason-code",
            decision_limit_reason_code,
        ],
        check=True,
    )


def _prepare_log_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("APPEND_LOGS", "0") != "1":
        path.write_text("", encoding="utf-8")


def _unavailable_artifact() -> Dict[str, Any]:
    return {
        "exit_code": 127,
        "decision_status": "unavailable",
        "post_gate_status": "unavailable",
        "stderr_tail": "execution command unavailable",
        "stdout_tail": "",
        "trace_tail": "",
        "trace_stage": "brain",
        "trace_reason": "command_unavailable",
        "repair_attempted": False,
        "repair_success": False,
    }


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Run kernel experiment pipeline in Python.")
    parser.add_argument("--config", default=None, help="Optional config.json path")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    root_dir = script_dir.parent
    runtime = _load_runtime_config(script_dir, root_dir)

    if args.config:
        custom_cfg = _read_json(Path(args.config))
        # Explicit override points only; keep compatibility defaults.
        if "num_runs" in custom_cfg:
            runtime = KernelRuntimeConfig(**{**runtime.__dict__, "num_runs": int(custom_cfg["num_runs"])})

    runtime.output_dir.mkdir(parents=True, exist_ok=True)
    runtime.artifact_dir.mkdir(parents=True, exist_ok=True)
    _prepare_log_file(runtime.log_file)

    for idx in range(1, runtime.num_runs + 1):
        if runtime.workspace_cleanup:
            subprocess.run(["bash", str(runtime.clean_script)], cwd=str(runtime.root_dir), check=True)

        run_id = f"kernel-{idx:03d}"
        artifact_path = runtime.artifact_dir / f"run_{run_id}.json"

        with tempfile.TemporaryDirectory(prefix=f"evolution_eval_{run_id}_") as td:
            stdout_path = Path(td) / "stdout.log"
            stderr_path = Path(td) / "stderr.log"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")

            executor = _build_shell_executor(
                root_dir=runtime.root_dir,
                execution_command=runtime.execution_command,
                decision_limit_override=runtime.decision_limit_override,
                debug=runtime.debug,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            repairer = _build_repairer(runtime.root_dir, runtime.repair_script, stdout_path, stderr_path)

            result = run_kernel_pipeline(
                {
                    "decision_limit_reason_code": runtime.decision_limit_reason_code,
                    "injection_applied": False,
                    "injection_mode": None,
                },
                str(artifact_path),
                executor=executor,
                repairer=repairer,
            )

        _append_marker(runtime.root_dir, "KERNEL_REPAIR_REASON_CODE", str(result["reason_code"]))
        _append_marker(runtime.root_dir, "KERNEL_REPAIR_TRIGGER", "TRUE" if result["repair_attempted"] else "FALSE")
        _append_marker(runtime.root_dir, "KERNEL_POST_REPAIR_STATUS", str(result["final_status"]))

        _invoke_run_logger(
            runtime.run_logger_script,
            runtime.log_file,
            run_id,
            artifact_path,
            runtime.decision_limit_reason_code,
        )

    print(f"[kernel] complete: {runtime.log_file}")
    return 0


def main() -> int:
    return _run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
