#!/usr/bin/env python3
"""Phase transition analysis for stability-temperature curves.

Assumptions:
- Input JSON is either a list of records or a dict containing `results`.
- Each record has `temperature` and stability metric in `stability_score` or `score`.
- Model id comes from record `model` or top-level `model` fallback.
"""

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit

from structural_fingerprint import structural_fingerprint

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_PLOT = True
except Exception:
    HAS_PLOT = False


def logistic_curve(tau: np.ndarray, a: float, tau_c: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(a * (tau - tau_c)))


def load_rows(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        default_model = payload.get("model", "unknown")
        rows = []
        for row in payload["results"]:
            if not isinstance(row, dict):
                continue
            out = dict(row)
            if "model" not in out:
                out["model"] = default_model
            rows.append(out)
        return rows
    return []


def _euclidean_distance(a: List[float], b: List[float]) -> float:
    return float(np.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))))


def _compute_svi(codes: List[str]) -> float:
    vectors = []
    for code in codes:
        fp = structural_fingerprint(code)
        vectors.append([float(v) for v in fp["fingerprint_vector"]])
    if not vectors:
        return 0.0
    centroid = np.mean(np.array(vectors, dtype=float), axis=0).tolist()
    dists = [_euclidean_distance(vec, centroid) for vec in vectors]
    return float(np.mean(dists)) if dists else 0.0


def prepare_curves(rows: List[dict]) -> Dict[str, Dict[str, np.ndarray]]:
    grouped: Dict[str, Dict[float, List[Tuple[float, float, str]]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = str(row.get("model", "unknown"))
        temp = row.get("temperature")
        stability = row.get("stability_score", row.get("score"))
        creativity = row.get("creativity_score", 0.0)
        code = row.get("code", "")
        try:
            t = float(temp)
            s = float(stability)
            c = float(creativity)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(model, {}).setdefault(t, []).append((s, c, str(code)))

    out: Dict[str, Dict[str, np.ndarray]] = {}
    for model, by_temp in grouped.items():
        tau = np.array(sorted(by_temp.keys()), dtype=float)
        s_vals = np.array([np.mean([v[0] for v in by_temp[t]]) for t in tau], dtype=float)
        c_vals = np.array([np.mean([v[1] for v in by_temp[t]]) for t in tau], dtype=float)
        svi_vals = np.array([_compute_svi([v[2] for v in by_temp[t]]) for t in tau], dtype=float)
        out[model] = {"tau": tau, "S": s_vals, "C": c_vals, "SVI": svi_vals}
    return out


def compute_derivatives(tau: np.ndarray, s_vals: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if tau.size < 2:
        return np.zeros_like(s_vals), np.zeros_like(s_vals)
    d1 = np.gradient(s_vals, tau)
    if tau.size < 3:
        return d1, np.zeros_like(d1)
    d2 = np.gradient(d1, tau)
    return d1, d2


def estimate_tau_c(tau: np.ndarray, d1: np.ndarray, d2: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    if tau.size == 0:
        return None, None
    t1 = float(tau[int(np.argmax(np.abs(d1)))]) if d1.size > 0 else None
    t2 = float(tau[int(np.argmax(np.abs(d2)))]) if d2.size > 0 else None
    return t1, t2


def fit_logistic_tau_c(tau: np.ndarray, s_vals: np.ndarray) -> Optional[float]:
    if tau.size < 3:
        return None
    x0 = np.array([5.0, float(np.median(tau))], dtype=float)
    try:
        bounds = ([-200.0, float(np.min(tau))], [200.0, float(np.max(tau))])
        params, _ = curve_fit(logistic_curve, tau, s_vals, p0=x0, bounds=bounds, maxfev=20000)
        return float(params[1])
    except Exception:
        return None


def bootstrap_tau_c(
    tau: np.ndarray,
    s_vals: np.ndarray,
    n_bootstrap: int = 500,
    seed: int = 42,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if tau.size < 3:
        return None, None, None
    rng = np.random.default_rng(seed)
    indices = np.arange(tau.size)
    estimates: List[float] = []
    for _ in range(n_bootstrap):
        sample_idx = np.sort(rng.choice(indices, size=tau.size, replace=True))
        tau_b = tau[sample_idx]
        s_b = s_vals[sample_idx]
        tau_u, inv_idx = np.unique(tau_b, return_inverse=True)
        s_u = np.zeros_like(tau_u, dtype=float)
        counts = np.zeros_like(tau_u, dtype=float)
        for i, g in enumerate(inv_idx):
            s_u[g] += s_b[i]
            counts[g] += 1.0
        s_u = s_u / np.maximum(counts, 1.0)
        d1, d2 = compute_derivatives(tau_u, s_u)
        est, _ = estimate_tau_c(tau_u, d1, d2)
        if est is not None:
            estimates.append(est)
    if not estimates:
        return None, None, None
    arr = np.array(estimates, dtype=float)
    return float(np.mean(arr)), float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def _pooled_std(values: np.ndarray) -> Optional[float]:
    if values.size < 2:
        return None
    std = float(np.std(values, ddof=1))
    if std < 0:
        return None
    return std


def analyze_model(
    model: str,
    tau: np.ndarray,
    s_vals: np.ndarray,
    c_vals: np.ndarray,
    svi_vals: np.ndarray,
    n_bootstrap: int = 500,
) -> Dict[str, object]:
    d1, d2 = compute_derivatives(tau, svi_vals)
    tau_c_d1, tau_c_d2 = estimate_tau_c(tau, d1, d2)
    tau_c_logistic = fit_logistic_tau_c(tau, svi_vals)
    tau_c_mean, ci_low, ci_high = bootstrap_tau_c(tau, svi_vals, n_bootstrap=n_bootstrap, seed=42)

    tau_c_index = int(np.argmax(np.abs(d2[1:-1]))) + 1 if d2.size >= 3 else None

    if tau_c_index is None or tau_c_index == 0 or tau_c_index == (len(tau) - 1):
        candidate_tau_c = None
        return {
            "model": model,
            "tau_c_first_derivative": tau_c_d1,
            "tau_c_second_derivative": tau_c_d2,
            "tau_c_logistic": tau_c_logistic,
            "tau_c_ci": [ci_low, ci_high],
            "transition_type": "no_transition",
            "candidate_tau_c": candidate_tau_c,
            "tau": [float(x) for x in tau],
            "stability": [float(x) for x in s_vals],
            "svi": [float(x) for x in svi_vals],
            "creativity": [float(x) for x in c_vals],
            "dS_dTau": [float(x) for x in d1],
            "d2S_dTau2": [float(x) for x in d2],
            "tau_c_bootstrap_mean": tau_c_mean,
        }

    candidate_tau_c = float(tau[tau_c_index])
    pooled_std = _pooled_std(svi_vals)
    delta_s = abs(float(svi_vals[tau_c_index + 1]) - float(svi_vals[tau_c_index - 1]))
    strong_jump = (pooled_std is not None and delta_s > (1.5 * pooled_std))

    tau_range = float(np.max(tau) - np.min(tau)) if tau.size > 1 else 0.0
    ci_width = (float(ci_high) - float(ci_low)) if (ci_low is not None and ci_high is not None) else None
    stable_bootstrap = (
        ci_width is not None
        and tau_range > 0.0
        and ci_width < (0.5 * tau_range)
        and ci_low is not None
        and ci_high is not None
        and float(ci_low) > float(np.min(tau))
        and float(ci_high) < float(np.max(tau))
    )

    transition = "discontinuous" if (strong_jump and stable_bootstrap) else "no_transition"

    return {
        "model": model,
        "tau_c_first_derivative": tau_c_d1,
        "tau_c_second_derivative": tau_c_d2,
        "tau_c_logistic": tau_c_logistic,
        "tau_c_ci": [ci_low, ci_high],
        "transition_type": transition,
        "candidate_tau_c": candidate_tau_c,
        "tau": [float(x) for x in tau],
        "stability": [float(x) for x in s_vals],
        "svi": [float(x) for x in svi_vals],
        "creativity": [float(x) for x in c_vals],
        "dS_dTau": [float(x) for x in d1],
        "d2S_dTau2": [float(x) for x in d2],
        "tau_c_bootstrap_mean": tau_c_mean,
    }


def plot_results(results: List[Dict[str, object]], out_path: str) -> None:
    if not HAS_PLOT:
        return
    fig, axes = plt.subplots(2, 1, figsize=(9, 8), sharex=True)
    ax_curve, ax_d2 = axes

    for item in results:
        model = str(item["model"])
        tau = np.array(item["tau"], dtype=float)
        svi_vals = np.array(item["svi"], dtype=float)
        d2 = np.array(item["d2S_dTau2"], dtype=float)
        ax_curve.plot(tau, svi_vals, marker="o", label=model)
        tau_c = item.get("candidate_tau_c")
        if tau_c is not None:
            ax_curve.axvline(float(tau_c), linestyle="--", alpha=0.35)
        ax_d2.plot(tau, d2, marker="o", label=model)

    ax_curve.set_ylabel("SVI")
    ax_curve.set_title("SVI vs Temperature")
    ax_curve.grid(True, alpha=0.25)
    ax_curve.legend(loc="best")

    ax_d2.set_xlabel("Temperature tau")
    ax_d2.set_ylabel("d2(SVI)/dtau2")
    ax_d2.set_title("Second Derivative of SVI")
    ax_d2.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def run_phase_transition(
    input_path: str,
    output_json_path: str,
    output_plot_path: str,
    n_bootstrap: int = 500,
) -> Dict[str, object]:
    rows = load_rows(input_path)
    curves = prepare_curves(rows)
    results = []
    for model in sorted(curves.keys()):
        data = curves[model]
        results.append(
            analyze_model(
                model=model,
                tau=data["tau"],
                s_vals=data["S"],
                c_vals=data["C"],
                svi_vals=data["SVI"],
                n_bootstrap=n_bootstrap,
            )
        )

    report: Dict[str, object] = {
        "bootstrap_iterations": int(n_bootstrap),
        "models": results,
    }

    os.makedirs(os.path.dirname(output_json_path) or ".", exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if results and HAS_PLOT:
        plot_results(results, output_plot_path)
    elif not HAS_PLOT:
        print("[ARE] Plotting disabled (matplotlib not installed).")

    return report


def main() -> int:
    input_path = "runtime/sweep_results.json"
    out_json = "runtime/phase_transition_report.json"
    out_plot = "runtime/phase_transition_plot.png"

    if len(sys.argv) > 1:
        input_path = sys.argv[1]
    if len(sys.argv) > 2:
        out_json = sys.argv[2]
    if len(sys.argv) > 3:
        out_plot = sys.argv[3]

    if not os.path.exists(input_path):
        print(f"input not found: {input_path}", file=sys.stderr)
        return 1

    run_phase_transition(
        input_path=input_path,
        output_json_path=out_json,
        output_plot_path=out_plot,
        n_bootstrap=500,
    )
    print(f"wrote {out_json}")
    print(f"wrote {out_plot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
