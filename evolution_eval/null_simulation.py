#!/usr/bin/env python3
"""Null-simulation utilities for evolution_eval.

These helpers generate synthetic rows under a no-effect assumption.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, List

AnalysisBuilder = Callable[[List[Dict[str, Any]], List[Dict[str, Any]], str], Dict[str, Any]]


def _is_success(row: Dict[str, Any]) -> bool:
    return str(row.get("status", "")).strip().upper() == "SUCCESS"


def simulate_null_experiment(
    baseline_rows: List[Dict[str, Any]],
    kernel_rows: List[Dict[str, Any]],
    rng: random.Random,
    build_analysis_result: AnalysisBuilder,
) -> Dict[str, float]:
    """Simulate one null draw where baseline/kernel are from same Bernoulli process."""
    all_rows = baseline_rows + kernel_rows
    if not all_rows:
        return {"p_value": 1.0, "cf_index": 0.0}

    p_success = sum(1 for r in all_rows if _is_success(r)) / len(all_rows)

    def gen_arm(template_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in template_rows:
            rr = dict(r)
            rr["status"] = "SUCCESS" if rng.random() < p_success else "FAIL"
            rr["reason_code"] = None if rr["status"] == "SUCCESS" else str(r.get("reason_code", "DEPENDENCY_ERROR"))
            rr["repair_success"] = bool(rr.get("repair_attempted", False)) and rr["status"] == "SUCCESS"
            out.append(rr)
        return out

    b_sim = gen_arm(baseline_rows)
    k_sim = gen_arm(kernel_rows)
    sim_result = build_analysis_result(b_sim, k_sim, "all")
    return {
        "p_value": float(sim_result["statistical_tests"]["injected_two_proportion_z_test"]["p_value"]),
        "cf_index": float(sim_result["counterfactual_analysis"]["cf_index"]),
    }


def run_null_simulations(
    baseline_rows: List[Dict[str, Any]],
    kernel_rows: List[Dict[str, Any]],
    n_sim: int,
    seed: int,
    build_analysis_result: AnalysisBuilder,
) -> Dict[str, Any]:
    """Run deterministic Monte Carlo null simulations and summarize distributions."""
    rng = random.Random(seed)
    p_values: List[float] = []
    cf_index_values: List[float] = []
    for _ in range(int(n_sim)):
        sim = simulate_null_experiment(baseline_rows, kernel_rows, rng, build_analysis_result)
        p_values.append(sim["p_value"])
        cf_index_values.append(sim["cf_index"])

    if n_sim <= 0:
        return {
            "n_simulations": 0,
            "seed": seed,
            "empirical_false_positive_rate": 0.0,
            "p_value_summary": {"min": 1.0, "q25": 1.0, "median": 1.0, "q75": 1.0, "max": 1.0, "mean": 1.0},
            "cf_index_summary": {"min": 0.0, "q25": 0.0, "median": 0.0, "q75": 0.0, "max": 0.0, "mean": 0.0},
            "histogram_ready": {"p_values": [], "cf_index": []},
        }

    s_p = sorted(p_values)
    s_cf = sorted(cf_index_values)

    def q(vals: List[float], frac: float) -> float:
        idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * frac))))
        return vals[idx]

    return {
        "n_simulations": int(n_sim),
        "seed": int(seed),
        "empirical_false_positive_rate": sum(1 for p in p_values if p < 0.05) / len(p_values),
        "p_value_summary": {
            "min": s_p[0], "q25": q(s_p, 0.25), "median": q(s_p, 0.5), "q75": q(s_p, 0.75), "max": s_p[-1],
            "mean": sum(p_values) / len(p_values),
        },
        "cf_index_summary": {
            "min": s_cf[0], "q25": q(s_cf, 0.25), "median": q(s_cf, 0.5), "q75": q(s_cf, 0.75), "max": s_cf[-1],
            "mean": sum(cf_index_values) / len(cf_index_values),
        },
        "histogram_ready": {
            "p_values": p_values,
            "cf_index": cf_index_values,
        },
    }
