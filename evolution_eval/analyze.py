#!/usr/bin/env python3
"""CLI entry and orchestration for evolution_eval analysis.

Statistical primitives are delegated to `stats_core.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Tuple

if SCRIPT_DIR := os.path.dirname(os.path.abspath(__file__)):
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)

from null_simulation import run_null_simulations as _run_null_simulations
from reproducibility import build_repro_report
from sensitivity import export_sweep_to_csv, run_sensitivity_sweep as _run_sensitivity_sweep
from stats_core import (
    cohens_h,
    estimate_required_sample_size,
    interpret_cohens_h,
    two_proportion_z_test,
    wilson_interval,
)
from transition_matrix import (
    build_matrix,
    compute_CI as tm_compute_CI,
    compute_causal_repair_lift,
    compute_effect_sizes as tm_compute_effect_sizes,
    compute_probabilities as tm_compute_probabilities,
    fisher_exact_two_sided,
    odds_ratio_with_ci,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def _parse_csv_ints(value: str) -> List[int]:
    return [int(x.strip()) for x in str(value).split(",") if x.strip()]


def _parse_csv_floats(value: str) -> List[float]:
    return [float(x.strip()) for x in str(value).split(",") if x.strip()]


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# IMPORTANT: analyze.py must not infer failure beyond run_logger records.
def norm_status(v: Any) -> str:
    return str(v).strip().upper()


def is_fail(row: Dict[str, Any]) -> bool:
    return norm_status(row.get("status", "")) == "FAIL"


def is_success(row: Dict[str, Any]) -> bool:
    return norm_status(row.get("status", "")) == "SUCCESS"


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_runs = len(rows)
    failures = [r for r in rows if is_fail(r)]
    failure_count = len(failures)
    failure_rate = (failure_count / total_runs) if total_runs else 0.0

    reason_distribution = dict(sorted(Counter(str(r.get("reason_code")) for r in rows).items()))
    failure_reason_distribution = dict(
        sorted(Counter(str(r.get("reason_code")) for r in failures if r.get("reason_code") is not None).items())
    )

    class_counts = Counter()
    for r in rows:
        if is_fail(r):
            if bool(r.get("hard_block", False)):
                class_counts["HARD_BLOCK"] += 1
            else:
                class_counts["FAIL"] += 1
        else:
            class_counts["SUCCESS"] += 1

    return {
        "total_runs": total_runs,
        "failure_count": failure_count,
        "failure_rate": failure_rate,
        "reason_code_distribution": reason_distribution,
        "classification_distribution": dict(sorted(class_counts.items())),
        "failure_reason_code_distribution": failure_reason_distribution,
    }


def summarize_injected(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    injected_rows = [r for r in rows if bool(r.get("injection_applied", False))]
    total_runs = len(injected_rows)
    failures = [r for r in injected_rows if is_fail(r)]
    failure_count = len(failures)
    failure_rate = (failure_count / total_runs) if total_runs else 0.0
    return {
        "total_runs": total_runs,
        "failure_count": failure_count,
        "failure_rate": failure_rate,
        "injection_applied_count": total_runs,
        "injection_mode_distribution": dict(
            sorted(Counter(str(r.get("injection_mode")) for r in injected_rows).items())
        ),
    }


def compute_stability_ratio(baseline: Dict[str, Any], kernel: Dict[str, Any]) -> float:
    merged = Counter()
    for k, v in baseline.get("classification_distribution", {}).items():
        merged[k] += int(v)
    for k, v in kernel.get("classification_distribution", {}).items():
        merged[k] += int(v)
    total = sum(merged.values())
    return (max(merged.values()) / total) if total else 0.0


def dominant_failure_signature(baseline: Dict[str, Any], kernel: Dict[str, Any]) -> str:
    merged = Counter()
    merged.update(baseline.get("failure_reason_code_distribution", {}))
    merged.update(kernel.get("failure_reason_code_distribution", {}))
    if not merged:
        return "NONE"
    return merged.most_common(1)[0][0]


def compute_repair_metrics(_baseline_rows: List[Dict[str, Any]], kernel_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    kernel_injected = [r for r in kernel_rows if bool(r.get("injection_applied", False))]
    total_injected_runs = len(kernel_injected)

    repair_attempts = [r for r in kernel_rows if bool(r.get("repair_attempted", False))]
    repair_successes = [r for r in kernel_rows if bool(r.get("repair_success", False))]

    total_repair_attempts = len(repair_attempts)
    total_repair_successes = len(repair_successes)

    tp_repairs = [
        r
        for r in kernel_rows
        if bool(r.get("repair_attempted", False))
        and bool(r.get("repair_success", False))
        and bool(r.get("injection_applied", False))
    ]
    true_positive_repairs = len(tp_repairs)

    total_injected_failures = sum(1 for r in kernel_injected if is_fail(r))

    return {
        "total_kernel_runs": len(kernel_rows),
        "total_injected_runs": total_injected_runs,
        "repair_attempts": total_repair_attempts,
        "repair_successes": total_repair_successes,
        "true_positive_repairs": true_positive_repairs,
        "total_injected_failures": total_injected_failures,
        "repair_attempt_rate": (total_repair_attempts / total_injected_runs) if total_injected_runs else 0.0,
        "repair_success_rate": (total_repair_successes / len(kernel_rows)) if kernel_rows else 0.0,
        "precision": (true_positive_repairs / total_repair_attempts) if total_repair_attempts else 0.0,
        "recall": (true_positive_repairs / total_injected_failures) if total_injected_failures else 0.0,
        "injection_applied_distribution": {
            "true": sum(1 for r in kernel_rows if bool(r.get("injection_applied", False))),
            "false": sum(1 for r in kernel_rows if not bool(r.get("injection_applied", False))),
        },
        "repair_attempt_vs_success": {
            "attempted": total_repair_attempts,
            "successful": total_repair_successes,
            "failed": max(0, total_repair_attempts - total_repair_successes),
        },
    }


def _filter_counterfactual_subset(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        r
        for r in rows
        if bool(r.get("injection_applied", False)) and str(r.get("reason_code", "")) == "DEPENDENCY_ERROR"
    ]


def _counterfactual_group_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    s2 = sum(1 for r in rows if is_success(r))
    p = (s2 / n) if n else 0.0
    ci = wilson_interval(s2, n)
    return {
        "total_runs": n,
        "s2_recovery_count": s2,
        "s2_recovery_rate": p,
        "s2_recovery_ci95": ci,
    }


def compute_counterfactual_analysis(baseline_rows: List[Dict[str, Any]], kernel_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    baseline_subset = _filter_counterfactual_subset(baseline_rows)
    kernel_subset = _filter_counterfactual_subset(kernel_rows)

    b = _counterfactual_group_stats(baseline_subset)
    k = _counterfactual_group_stats(kernel_subset)

    cf_index = k["s2_recovery_rate"] - b["s2_recovery_rate"]
    effect_direction = "positive" if cf_index > 0 else ("negative" if cf_index < 0 else "neutral")
    h_signed = cohens_h(k["s2_recovery_rate"], b["s2_recovery_rate"])

    nb = b["total_runs"]
    nk = k["total_runs"]
    pb = b["s2_recovery_rate"]
    pk = k["s2_recovery_rate"]
    if nb > 0 and nk > 0:
        z = 1.959963984540054
        se = ((pb * (1.0 - pb) / nb) + (pk * (1.0 - pk) / nk)) ** 0.5
        cf_ci = {"lower": cf_index - z * se, "upper": cf_index + z * se} if se > 0 else {"lower": cf_index, "upper": cf_index}
    else:
        cf_ci = {"lower": 0.0, "upper": 0.0}

    a = k["s2_recovery_count"]
    b_fail = max(0, k["total_runs"] - k["s2_recovery_count"])
    c = b["s2_recovery_count"]
    d_fail = max(0, b["total_runs"] - b["s2_recovery_count"])
    small_cell = min(a, b_fail, c, d_fail) < 5 if (k["total_runs"] and b["total_runs"]) else True
    fisher_p = fisher_exact_two_sided(a, b_fail, c, d_fail) if (a + b_fail + c + d_fail) > 0 else 1.0

    return {
        "matched_subset_definition": {"injection_applied": True, "reason_code": "DEPENDENCY_ERROR"},
        "baseline": b,
        "kernel": k,
        "conditional_transition": {
            "metric": "P(S2 | DEPENDENCY_ERROR)",
            "baseline": b["s2_recovery_rate"],
            "kernel": k["s2_recovery_rate"],
        },
        "cf_index": cf_index,
        "cf_index_ci95": cf_ci,
        "effect_direction": effect_direction,
        "effect_size": {
            "cohens_h_signed": h_signed,
            "interpretation": interpret_cohens_h(h_signed),
        },
        "fisher_fallback": {"used": small_cell, "p_value": fisher_p},
    }


def apply_causal_mode(rows: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    if mode == "injected_only":
        return [r for r in rows if bool(r.get("injection_applied", False))]
    return rows


def build_analysis_result(
    baseline_rows: List[Dict[str, Any]],
    kernel_rows: List[Dict[str, Any]],
    causal_mode: str = "all",
) -> Dict[str, Any]:
    baseline_rows_mode = apply_causal_mode(baseline_rows, causal_mode)
    kernel_rows_mode = apply_causal_mode(kernel_rows, causal_mode)

    baseline = summarize(baseline_rows_mode)
    kernel = summarize(kernel_rows_mode)

    baseline_injected = summarize_injected(baseline_rows)
    kernel_injected = summarize_injected(kernel_rows)

    absolute_improvement = baseline["failure_rate"] - kernel["failure_rate"]
    relative_improvement = (absolute_improvement / baseline["failure_rate"]) if baseline["failure_rate"] > 0 else 0.0

    injected_absolute_improvement = baseline_injected["failure_rate"] - kernel_injected["failure_rate"]
    injected_relative_improvement = (
        (injected_absolute_improvement / baseline_injected["failure_rate"])
        if baseline_injected["failure_rate"] > 0
        else 0.0
    )

    ci = {
        "baseline_failure_rate": wilson_interval(baseline["failure_count"], baseline["total_runs"]),
        "kernel_failure_rate": wilson_interval(kernel["failure_count"], kernel["total_runs"]),
        "injected_baseline_failure_rate": wilson_interval(baseline_injected["failure_count"], baseline_injected["total_runs"]),
        "injected_kernel_failure_rate": wilson_interval(kernel_injected["failure_count"], kernel_injected["total_runs"]),
    }

    z_all = two_proportion_z_test(
        baseline["failure_count"], baseline["total_runs"], kernel["failure_count"], kernel["total_runs"]
    )
    z_inj = two_proportion_z_test(
        baseline_injected["failure_count"], baseline_injected["total_runs"],
        kernel_injected["failure_count"], kernel_injected["total_runs"],
    )

    a = kernel_injected["failure_count"]
    b = max(0, kernel_injected["total_runs"] - kernel_injected["failure_count"])
    c = baseline_injected["failure_count"]
    d = max(0, baseline_injected["total_runs"] - baseline_injected["failure_count"])
    small_cell = min(a, b, c, d) < 5 if (baseline_injected["total_runs"] and kernel_injected["total_runs"]) else True
    fisher_p = fisher_exact_two_sided(a, b, c, d) if (a + b + c + d) > 0 else 1.0

    repair = compute_repair_metrics(baseline_rows, kernel_rows)

    overall_h = cohens_h(baseline["failure_rate"], kernel["failure_rate"])
    injected_h = cohens_h(baseline_injected["failure_rate"], kernel_injected["failure_rate"])

    baseline_tm = build_matrix(apply_causal_mode(baseline_rows, causal_mode))
    kernel_tm = build_matrix(apply_causal_mode(kernel_rows, causal_mode))
    baseline_tm_prob = tm_compute_probabilities(baseline_tm)
    kernel_tm_prob = tm_compute_probabilities(kernel_tm)

    transition = {
        "baseline": {
            "counts": baseline_tm["transition_counts"],
            "probabilities": baseline_tm_prob,
            "confidence_intervals": tm_compute_CI(baseline_tm),
        },
        "kernel": {
            "counts": kernel_tm["transition_counts"],
            "probabilities": kernel_tm_prob,
            "confidence_intervals": tm_compute_CI(kernel_tm),
        },
        "effect_sizes": tm_compute_effect_sizes(baseline_tm_prob, kernel_tm_prob),
        "causal_repair_lift_kernel": compute_causal_repair_lift(apply_causal_mode(kernel_rows, causal_mode)),
        "odds_ratio_kernel": odds_ratio_with_ci(apply_causal_mode(kernel_rows, causal_mode)),
    }

    counterfactual_analysis = compute_counterfactual_analysis(
        apply_causal_mode(baseline_rows, causal_mode),
        apply_causal_mode(kernel_rows, causal_mode),
    )

    result = {
        "baseline": baseline,
        "kernel": kernel,
        "injected": {
            "baseline_injected": baseline_injected,
            "kernel_injected": kernel_injected,
            "injection_applied_counts": {
                "baseline": baseline_injected["injection_applied_count"],
                "kernel": kernel_injected["injection_applied_count"],
            },
        },
        "repair": repair,
        "confidence_intervals": ci,
        "statistical_tests": {
            "overall_two_proportion_z_test": z_all,
            "injected_two_proportion_z_test": z_inj,
            "injected_fisher_exact": {
                "used_as_fallback": small_cell,
                "p_value": fisher_p,
            },
        },
        "improvement": {
            "absolute_improvement": absolute_improvement,
            "relative_improvement": relative_improvement,
            "improvement_injected_absolute": injected_absolute_improvement,
            "improvement_injected_relative": injected_relative_improvement,
        },
        "effect_size": {
            "overall": {"h": overall_h, "interpretation": interpret_cohens_h(overall_h)},
            "injected": {"h": injected_h, "interpretation": interpret_cohens_h(injected_h)},
        },
        "counterfactual_analysis": counterfactual_analysis,
        "transition_analysis": transition,
        "power_estimation": {
            "required_sample_size_per_group_for_injected_delta": estimate_required_sample_size(
                baseline_injected["failure_rate"], kernel_injected["failure_rate"], alpha=0.05, power=0.8
            )
        },
        "causal_mode": causal_mode,
        "baseline_injected": baseline_injected,
        "kernel_injected": kernel_injected,
        "dominant_failure_signature": dominant_failure_signature(baseline, kernel),
        "stability_ratio": compute_stability_ratio(baseline, kernel),
    }
    return result


def run_sensitivity_sweep(
    sample_sizes: List[int],
    baseline_rates: List[float],
    kernel_rates: List[float],
    injection_rates: List[float],
    n_simulations: int,
    seed: int,
) -> Dict[str, Any]:
    return _run_sensitivity_sweep(
        sample_sizes=sample_sizes,
        baseline_rates=baseline_rates,
        kernel_rates=kernel_rates,
        injection_rates=injection_rates,
        n_simulations=n_simulations,
        seed=seed,
        build_analysis_result=build_analysis_result,
    )


def run_null_simulations(
    baseline_rows: List[Dict[str, Any]],
    kernel_rows: List[Dict[str, Any]],
    n_sim: int,
    seed: int,
) -> Dict[str, Any]:
    return _run_null_simulations(
        baseline_rows=baseline_rows,
        kernel_rows=kernel_rows,
        n_sim=n_sim,
        seed=seed,
        build_analysis_result=build_analysis_result,
    )


def write_outputs(result: Dict[str, Any], cfg: Dict[str, Any], out_dir: str) -> Tuple[str, str]:
    analysis_json = os.path.join(out_dir, cfg["logs"]["analysis"])
    analysis_extended_json = os.path.join(out_dir, cfg["logs"].get("analysis_extended", "analysis_extended.json"))

    analysis = {
        "config": {
            "num_runs": cfg.get("num_runs"),
            "failure_definition": cfg.get("failure_definition"),
            "execution_command": cfg.get("execution_command"),
            "causal_mode": result.get("causal_mode", "all"),
        },
        "baseline": result["baseline"],
        "kernel": result["kernel"],
        "comparison": {
            "absolute_improvement": result["improvement"]["absolute_improvement"],
            "relative_improvement": result["improvement"]["relative_improvement"],
        },
    }

    with open(analysis_json, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    with open(analysis_extended_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    return analysis_json, analysis_extended_json


def run_main(args: argparse.Namespace) -> int:
    cfg = read_json(CONFIG_PATH)
    out_dir = resolve_path(cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)

    baseline_log = args.baseline_log or os.path.join(out_dir, cfg["logs"]["baseline"])
    kernel_log = args.kernel_log or os.path.join(out_dir, cfg["logs"]["kernel"])

    baseline_rows = load_jsonl(baseline_log)
    kernel_rows = load_jsonl(kernel_log)

    result = build_analysis_result(baseline_rows, kernel_rows, causal_mode=args.causal_mode)

    repro_seed = args.sweep_seed if args.sensitivity_sweep else (args.null_sim_seed if args.null_sim_seed is not None else 0)
    if args.null_sim > 0:
        seed = args.null_sim_seed if args.null_sim_seed is not None else int(read_json(CONFIG_PATH).get("failure_injection", {}).get("seed", 20260216))
        result["null_simulation"] = run_null_simulations(baseline_rows, kernel_rows, args.null_sim, seed)
        repro_seed = seed

    sweep_payload = None
    if args.sensitivity_sweep:
        sweep_payload = run_sensitivity_sweep(
            sample_sizes=_parse_csv_ints(args.sweep_sample_sizes),
            baseline_rates=_parse_csv_floats(args.sweep_baseline_rates),
            kernel_rates=_parse_csv_floats(args.sweep_kernel_rates),
            injection_rates=_parse_csv_floats(args.sweep_injection_rates),
            n_simulations=int(args.sweep_sim),
            seed=int(args.sweep_seed),
        )
        result["sensitivity_sweep"] = sweep_payload
        if args.sweep_output:
            export_sweep_to_csv(sweep_payload, args.sweep_output)

    analysis_json, analysis_extended_json = write_outputs(result, cfg, out_dir)

    if args.repro_report:
        modules = [
            os.path.join(SCRIPT_DIR, "analyze.py"),
            os.path.join(SCRIPT_DIR, "stats_core.py"),
            os.path.join(SCRIPT_DIR, "null_simulation.py"),
            os.path.join(SCRIPT_DIR, "sensitivity.py"),
            os.path.join(SCRIPT_DIR, "reproducibility.py"),
            os.path.join(SCRIPT_DIR, "transition_matrix.py"),
        ]
        repro = build_repro_report(
            config=cfg,
            input_files=[baseline_log, kernel_log],
            module_files=modules,
            seed=int(repro_seed),
            sweep_payload=sweep_payload,
        )
        with open(args.repro_report, "w", encoding="utf-8") as f:
            json.dump(repro, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

    print(f"Analysis written: {analysis_json}")
    print(f"Extended analysis written: {analysis_extended_json}")
    print(f"Baseline failures: {result['baseline']['failure_count']}/{result['baseline']['total_runs']} ({result['baseline']['failure_rate']:.2%})")
    print(f"Kernel failures:   {result['kernel']['failure_count']}/{result['kernel']['total_runs']} ({result['kernel']['failure_rate']:.2%})")
    print(f"Absolute improvement: {result['improvement']['absolute_improvement']:.2%}")
    print(f"Injected p-value (z-test): {result['statistical_tests']['injected_two_proportion_z_test']['p_value']:.6f}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze evolution_eval runs")
    p.add_argument("--baseline-log", default="", help="Optional baseline JSONL path")
    p.add_argument("--kernel-log", default="", help="Optional kernel JSONL path")
    p.add_argument("--causal-mode", default="all", choices=["all", "injected_only"], help="Causal isolation mode")
    p.add_argument("--null-sim", type=int, default=0, help="Run Monte Carlo null simulations")
    p.add_argument("--null-sim-seed", type=int, default=None, help="Seed for null simulation determinism")
    p.add_argument("--sensitivity-sweep", action="store_true", help="Run sensitivity parameter sweep simulation")
    p.add_argument("--sweep-sample-sizes", default="20,50,100,200")
    p.add_argument("--sweep-baseline-rates", default="0.4,0.5")
    p.add_argument("--sweep-kernel-rates", default="0.5,0.6")
    p.add_argument("--sweep-injection-rates", default="0.3,0.5")
    p.add_argument("--sweep-sim", type=int, default=500)
    p.add_argument("--sweep-seed", type=int, default=42)
    p.add_argument("--repro-report", default="", help="Optional reproducibility report JSON path")
    p.add_argument("--sweep-output", default="", help="Optional CSV output path for sensitivity sweep")
    return p.parse_args()


def main() -> int:
    return run_main(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
