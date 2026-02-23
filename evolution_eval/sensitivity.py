#!/usr/bin/env python3
"""Sensitivity sweep utilities for evolution_eval."""

from __future__ import annotations

import csv
import random
from typing import Any, Callable, Dict, List, Tuple

from stats_core import theoretical_two_proportion_power

AnalysisBuilder = Callable[[List[Dict[str, Any]], List[Dict[str, Any]], str], Dict[str, Any]]


def _combo_seed(base_seed: int, combo: Tuple[int, float, float, float]) -> int:
    n, br, kr, ir = combo
    key = f"{base_seed}:{n}:{br:.8f}:{kr:.8f}:{ir:.8f}"
    return int.from_bytes(key.encode("utf-8"), "little", signed=False) % (2**32)


def _simulate_rows(*, n: int, failure_rate: float, injection_rate: float, rng: random.Random, arm: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    fr = max(0.0, min(1.0, float(failure_rate)))
    ir = max(0.0, min(1.0, float(injection_rate)))
    for i in range(n):
        injected = rng.random() < ir
        fail = rng.random() < fr
        status = "FAIL" if fail else "SUCCESS"
        rows.append(
            {
                "run_id": f"{arm}-{i:04d}",
                "status": status,
                "reason_code": "DEPENDENCY_ERROR" if injected else (None if status == "SUCCESS" else "OTHER_ERROR"),
                "hard_block": False,
                "injection_applied": injected,
                "injection_mode": "dependency_error" if injected else None,
                "repair_attempted": injected,
                "repair_success": bool(injected and status == "SUCCESS"),
            }
        )
    return rows


def run_sensitivity_sweep(
    sample_sizes: List[int],
    baseline_rates: List[float],
    kernel_rates: List[float],
    injection_rates: List[float],
    n_simulations: int,
    seed: int,
    build_analysis_result: AnalysisBuilder,
) -> Dict[str, Any]:
    """Run deterministic sweep with empirical + theoretical power calibration."""
    alpha = 0.05
    results: List[Dict[str, Any]] = []

    for n in sample_sizes:
        for br in baseline_rates:
            for kr in kernel_rates:
                for ir in injection_rates:
                    combo = (int(n), float(br), float(kr), float(ir))
                    rng_null = random.Random(_combo_seed(seed, combo) + 17)
                    rng_alt = random.Random(_combo_seed(seed, combo) + 31)

                    null_p_values: List[float] = []
                    alt_p_values: List[float] = []
                    cf_values: List[float] = []
                    h_values: List[float] = []
                    or_values: List[float] = []

                    for _ in range(int(n_simulations)):
                        b_null = _simulate_rows(n=int(n), failure_rate=float(br), injection_rate=float(ir), rng=rng_null, arm="bnull")
                        k_null = _simulate_rows(n=int(n), failure_rate=float(br), injection_rate=float(ir), rng=rng_null, arm="knull")
                        null_res = build_analysis_result(b_null, k_null, "all")
                        null_p_values.append(float(null_res["statistical_tests"]["injected_two_proportion_z_test"]["p_value"]))

                        b_alt = _simulate_rows(n=int(n), failure_rate=float(br), injection_rate=float(ir), rng=rng_alt, arm="balt")
                        k_alt = _simulate_rows(n=int(n), failure_rate=float(kr), injection_rate=float(ir), rng=rng_alt, arm="kalt")
                        alt_res = build_analysis_result(b_alt, k_alt, "all")
                        alt_p_values.append(float(alt_res["statistical_tests"]["injected_two_proportion_z_test"]["p_value"]))
                        cf_values.append(float(alt_res["counterfactual_analysis"]["cf_index"]))
                        h_values.append(float(alt_res["counterfactual_analysis"]["effect_size"]["cohens_h_signed"]))
                        or_values.append(float(alt_res["transition_analysis"]["odds_ratio_kernel"]["odds_ratio"]))

                    cf_mean = (sum(cf_values) / len(cf_values)) if cf_values else 0.0
                    cf_var = (sum((x - cf_mean) ** 2 for x in cf_values) / len(cf_values)) if cf_values else 0.0
                    empirical_power = (sum(1 for p in alt_p_values if p < alpha) / len(alt_p_values)) if alt_p_values else 0.0
                    theoretical_power = theoretical_two_proportion_power(int(n), float(br), float(kr), alpha=alpha)

                    results.append(
                        {
                            "sample_size": int(n),
                            "baseline_rate": float(br),
                            "kernel_rate": float(kr),
                            "injection_rate": float(ir),
                            "fpr": (sum(1 for p in null_p_values if p < alpha) / len(null_p_values)) if null_p_values else 0.0,
                            "power": empirical_power,
                            "theoretical_power": theoretical_power,
                            "power_gap": abs(empirical_power - theoretical_power),
                            "cf_index_mean": cf_mean,
                            "cf_index_var": cf_var,
                            "cohens_h_mean": (sum(h_values) / len(h_values)) if h_values else 0.0,
                            "or_mean": (sum(or_values) / len(or_values)) if or_values else 0.0,
                        }
                    )

    results.sort(key=lambda r: (r["sample_size"], r["baseline_rate"], r["kernel_rate"], r["injection_rate"]))
    return {
        "sweep_config": {
            "sample_sizes": [int(x) for x in sample_sizes],
            "baseline_rates": [float(x) for x in baseline_rates],
            "kernel_rates": [float(x) for x in kernel_rates],
            "injection_rates": [float(x) for x in injection_rates],
            "n_simulations": int(n_simulations),
            "seed": int(seed),
        },
        "results": results,
    }


def export_sweep_to_csv(sweep: Dict[str, Any], path: str) -> None:
    """Export sweep results with deterministic ordering and rounded numeric precision."""
    rows = list(sweep.get("results", []))
    rows.sort(key=lambda r: (r["sample_size"], r["baseline_rate"], r["kernel_rate"], r["injection_rate"]))
    fieldnames = [
        "sample_size",
        "baseline_rate",
        "kernel_rate",
        "injection_rate",
        "fpr",
        "power",
        "theoretical_power",
        "power_gap",
        "cf_index_mean",
        "cf_index_var",
        "cohens_h_mean",
        "or_mean",
    ]

    def norm(value: Any) -> Any:
        if isinstance(value, float):
            return f"{value:.6f}"
        return value

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: norm(row.get(k)) for k in fieldnames})
