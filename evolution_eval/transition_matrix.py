#!/usr/bin/env python3
"""Transition-state modeling for repair experiments (JSONL source-of-truth only)."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, Tuple


STATES = ("S0", "S1", "S2", "S3")
# S0=initial FAIL, S1=repair_attempted, S2=final SUCCESS, S3=final FAIL


def _norm_status(value: Any) -> str:
    return str(value).strip().upper()


def _validate_row(row: Dict[str, Any]) -> None:
    """Validate minimal semantic consistency of a run row."""
    status = _norm_status(row.get("status", ""))
    if status not in {"SUCCESS", "FAIL"}:
        raise ValueError(f"invalid status: {status!r}")

    repair_attempted = bool(row.get("repair_attempted", False))
    repair_success = bool(row.get("repair_success", False))
    if repair_success and not repair_attempted:
        raise ValueError("repair_success cannot be true when repair_attempted is false")


def wilson_ci(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Wilson 95% CI for a binomial proportion."""
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    margin = (z / den) * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n)))
    return max(0.0, center - margin), min(1.0, center + margin)


def cohens_h_signed(p1: float, p2: float) -> float:
    """Signed Cohen's h = h(p1,p2), preserving direction p1 - p2."""
    p1 = max(0.0, min(1.0, float(p1)))
    p2 = max(0.0, min(1.0, float(p2)))
    return 2.0 * (math.asin(math.sqrt(p1)) - math.asin(math.sqrt(p2)))


def interpret_signed_h(h: float) -> str:
    """Magnitude + direction interpretation for signed effect sizes."""
    ah = abs(h)
    if ah < 0.2:
        mag = "negligible"
    elif ah < 0.5:
        mag = "small"
    elif ah < 0.8:
        mag = "medium"
    else:
        mag = "large"
    direction = "positive" if h > 0 else ("negative" if h < 0 else "neutral")
    return f"{mag}_{direction}"


def build_matrix(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build transition counts from run rows using only JSONL fields.

    Transitions counted:
      S0->S1 : failed run with repair_attempted=true
      S0->S3 : failed run with repair_attempted=false
      S1->S2 : repair_attempted=true and final status SUCCESS
      S1->S3 : repair_attempted=true and final status FAIL
      S0->S2 : collapsed success path from failure-origin runs after repair
    """
    counts = Counter()
    origin_counts = Counter()

    for r in rows:
        _validate_row(r)
        status = _norm_status(r.get("status", ""))
        repair_attempted = bool(r.get("repair_attempted", False))

        if status == "FAIL":
            origin_counts["S0"] += 1
            if repair_attempted:
                counts["S0->S1"] += 1
                origin_counts["S1"] += 1
                counts["S1->S3"] += 1
                counts["S0->S3"] += 1
            else:
                counts["S0->S3"] += 1
        elif status == "SUCCESS":
            # Success contributes to post-repair success only when repair was attempted.
            if repair_attempted:
                counts["S0->S1"] += 1
                origin_counts["S0"] += 1
                origin_counts["S1"] += 1
                counts["S1->S2"] += 1
                counts["S0->S2"] += 1

    return {
        "states": list(STATES),
        "transition_counts": dict(sorted(counts.items())),
        "origin_counts": dict(sorted(origin_counts.items())),
    }


def compute_probabilities(matrix: Dict[str, Any]) -> Dict[str, Any]:
    """Compute transition probabilities and Wilson 95% CI from transition counts."""
    counts = matrix.get("transition_counts", {})
    origins = matrix.get("origin_counts", {})

    out: Dict[str, Any] = {}
    for transition in ("S0->S1", "S0->S2", "S0->S3", "S1->S2", "S1->S3"):
        src, _ = transition.split("->", 1)
        n = int(origins.get(src, 0))
        k = int(counts.get(transition, 0))
        p = (k / n) if n else 0.0
        lo, hi = wilson_ci(k, n)
        out[transition] = {
            "count": k,
            "denominator": n,
            "probability": p,
            "ci95_lower": lo,
            "ci95_upper": hi,
        }
    return out


def compute_CI(matrix: Dict[str, Any]) -> Dict[str, Any]:
    """Alias for CI-focused reporting compatibility."""
    return compute_probabilities(matrix)


def compute_effect_sizes(
    baseline_probabilities: Dict[str, Any],
    kernel_probabilities: Dict[str, Any],
) -> Dict[str, Any]:
    """Signed Cohen's h (kernel - baseline) for key transitions."""
    result: Dict[str, Any] = {}
    for key in ("S1->S2", "S0->S2"):
        bp = float(baseline_probabilities.get(key, {}).get("probability", 0.0))
        kp = float(kernel_probabilities.get(key, {}).get("probability", 0.0))
        h = cohens_h_signed(kp, bp)
        result[key] = {
            "baseline_probability": bp,
            "kernel_probability": kp,
            "cohens_h_signed": h,
            "interpretation": interpret_signed_h(h),
        }
    return result


def compute_causal_repair_lift(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute P(S2|repair)-P(S2|no_repair) from rows."""
    rows_list = list(rows)
    rep = [r for r in rows_list if bool(r.get("repair_attempted", False))]
    no_rep = [r for r in rows_list if not bool(r.get("repair_attempted", False))]

    rep_success = sum(1 for r in rep if _norm_status(r.get("status", "")) == "SUCCESS")
    no_rep_success = sum(1 for r in no_rep if _norm_status(r.get("status", "")) == "SUCCESS")

    p_rep = (rep_success / len(rep)) if rep else 0.0
    p_no_rep = (no_rep_success / len(no_rep)) if no_rep else 0.0

    return {
        "n_repair": len(rep),
        "n_no_repair": len(no_rep),
        "success_with_repair": rep_success,
        "success_without_repair": no_rep_success,
        "p_success_given_repair": p_rep,
        "p_success_given_no_repair": p_no_rep,
        "causal_repair_lift": p_rep - p_no_rep,
    }


def _log_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _hypergeom_p(a: int, r1: int, c1: int, n: int) -> float:
    # P(A=a) with fixed marginals.
    return math.exp(_log_comb(c1, a) + _log_comb(n - c1, r1 - a) - _log_comb(n, r1))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for 2x2 table."""
    r1 = a + b
    r2 = c + d
    c1 = a + c
    n = r1 + r2
    amin = max(0, r1 - (n - c1))
    amax = min(r1, c1)

    p_obs = _hypergeom_p(a, r1, c1, n)
    p = 0.0
    for x in range(amin, amax + 1):
        px = _hypergeom_p(x, r1, c1, n)
        if px <= p_obs + 1e-15:
            p += px
    return min(1.0, max(0.0, p))


def odds_ratio_with_ci(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Odds ratio of SUCCESS for repair_attempted vs no_repair with CI.
    Uses Haldane-Anscombe 0.5 correction for CI stability.
    """
    rows_list = list(rows)
    rep = [r for r in rows_list if bool(r.get("repair_attempted", False))]
    no_rep = [r for r in rows_list if not bool(r.get("repair_attempted", False))]

    a = sum(1 for r in rep if _norm_status(r.get("status", "")) == "SUCCESS")
    b = sum(1 for r in rep if _norm_status(r.get("status", "")) != "SUCCESS")
    c = sum(1 for r in no_rep if _norm_status(r.get("status", "")) == "SUCCESS")
    d = sum(1 for r in no_rep if _norm_status(r.get("status", "")) != "SUCCESS")

    ac, bc, cc, dc = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    or_hat = (ac * dc) / (bc * cc)
    se = math.sqrt((1.0 / ac) + (1.0 / bc) + (1.0 / cc) + (1.0 / dc))
    z = 1.959963984540054
    lo = math.exp(math.log(or_hat) - z * se)
    hi = math.exp(math.log(or_hat) + z * se)

    small_cell = min(a, b, c, d) < 5
    fisher_p = fisher_exact_two_sided(a, b, c, d)

    return {
        "table": {
            "repair_success": a,
            "repair_fail": b,
            "no_repair_success": c,
            "no_repair_fail": d,
        },
        "odds_ratio": or_hat,
        "ci95_lower": lo,
        "ci95_upper": hi,
        "small_cell_detected": small_cell,
        "fisher_exact_p_value": fisher_p,
    }
