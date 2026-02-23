#!/usr/bin/env python3
import json
import math
import os
import random
import sys
from collections import defaultdict

from structural_fingerprint import FEATURE_ORDER, structural_fingerprint


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_key_temp(temp):
    numeric = to_float(temp)
    if numeric is None:
        return (1, str(temp))
    return (0, numeric)


def format_f3(value):
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


def round3(value):
    if value is None:
        return None
    return round(float(value), 3)


def euclidean_distance(a, b):
    if len(a) != len(b):
        return None
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def vector_mean(vectors):
    if not vectors:
        return []
    dim = len(vectors[0])
    sums = [0.0] * dim
    for vec in vectors:
        if len(vec) != dim:
            return []
        for i, value in enumerate(vec):
            sums[i] += float(value)
    n = float(len(vectors))
    return [s / n for s in sums]


def variance(values):
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return sum((x - m) ** 2 for x in values) / (n - 1)


def anova_one_way(groups):
    clean = [g for g in groups if len(g) > 0]
    k = len(clean)
    n_total = sum(len(g) for g in clean)
    if k < 2 or n_total <= k:
        return {
            "f_stat": None,
            "ss_between": None,
            "ss_within": None,
            "df_between": None,
            "df_within": None,
        }

    all_values = [x for g in clean for x in g]
    grand_mean = sum(all_values) / n_total

    ss_between = 0.0
    ss_within = 0.0
    for g in clean:
        m = sum(g) / len(g)
        ss_between += len(g) * ((m - grand_mean) ** 2)
        ss_within += sum((x - m) ** 2 for x in g)

    df_between = k - 1
    df_within = n_total - k
    ms_between = ss_between / df_between if df_between > 0 else None
    ms_within = ss_within / df_within if df_within > 0 else None

    if ms_between is None or ms_within in (None, 0.0):
        f_stat = None
    else:
        f_stat = ms_between / ms_within

    return {
        "f_stat": f_stat,
        "ss_between": ss_between,
        "ss_within": ss_within,
        "df_between": df_between,
        "df_within": df_within,
    }


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def bootstrap_ci(values, n_resamples=1000, seed=42):
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return percentile(means, 0.025), percentile(means, 0.975)


def build_structural_report(temp_keys, per_temperature, comparisons):
    lines = []
    lines.append("# Structural Stability Report")
    lines.append("")
    lines.append("## Per-temperature Metrics")
    lines.append("")
    lines.append("| Temperature | SVI | CI Low | CI High | ast_depth_var | branch_ratio_var | class_count | function_count | lambda_count | yield_count | recursion_rate | inheritance_edges | import_count | valid_runs |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t in temp_keys:
        row = per_temperature[t]
        ci = row.get("svi_bootstrap_ci_95", {})
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                t,
                format_f3(row.get("svi")),
                format_f3(ci.get("low")),
                format_f3(ci.get("high")),
                format_f3(row.get("ast_max_depth_variance")),
                format_f3(row.get("branch_node_ratio_variance")),
                format_f3(row.get("class_count")),
                format_f3(row.get("function_count")),
                format_f3(row.get("lambda_count")),
                format_f3(row.get("yield_count")),
                format_f3(row.get("recursion_rate")),
                format_f3(row.get("inheritance_edges")),
                format_f3(row.get("import_count")),
                row.get("valid_runs", 0),
            )
        )

    lines.append("")
    lines.append("## Across-temperature Tests")
    lines.append("")
    anova = comparisons.get("anova_svi", {})
    lines.append(f"- ANOVA F-statistic: {format_f3(anova.get('f_stat'))}")
    lines.append(f"- eta_squared: {format_f3(comparisons.get('eta_squared'))}")
    lev = comparisons.get("levene_style_svi", {})
    lines.append(f"- Levene-style F-statistic: {format_f3(lev.get('f_stat'))}")

    return "\n".join(lines) + "\n"


def main():
    runtime_dir = "runtime"
    input_path = os.path.join(runtime_dir, "sweep_results.json")
    output_json = os.path.join(runtime_dir, "structural_stats.json")
    output_md = os.path.join(runtime_dir, "structural_report.md")

    if not os.path.exists(input_path):
        print("missing runtime/sweep_results.json", file=sys.stderr)
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        rows = payload["results"]
    elif isinstance(payload, list):
        rows = payload
    else:
        print("invalid sweep_results schema", file=sys.stderr)
        return 1

    grouped = defaultdict(list)
    grouped_depth = defaultdict(list)
    grouped_branch_ratio = defaultdict(list)
    grouped_class = defaultdict(list)
    grouped_function = defaultdict(list)
    grouped_lambda = defaultdict(list)
    grouped_yield = defaultdict(list)
    grouped_recursion = defaultdict(list)
    grouped_inheritance = defaultdict(list)
    grouped_import = defaultdict(list)
    parse_error_count = 0

    feature_names = list(FEATURE_ORDER)
    idx_depth = feature_names.index("ast_max_depth")
    idx_branch = feature_names.index("branch_node_ratio")
    idx_class = feature_names.index("class_count")
    idx_function = feature_names.index("function_count")
    idx_lambda = feature_names.index("lambda_count")
    idx_yield = feature_names.index("yield_count")
    idx_recursion = feature_names.index("recursion_flag")
    idx_inheritance = feature_names.index("inheritance_edges")
    idx_import = feature_names.index("import_count")

    for row in rows:
        if not isinstance(row, dict):
            continue
        temp = row.get("temperature")
        code = row.get("code")
        if temp is None or not isinstance(code, str):
            continue

        fp = structural_fingerprint(code)
        vec = [float(v) for v in fp["fingerprint_vector"]]
        tkey = str(temp)
        grouped[tkey].append(vec)
        grouped_depth[tkey].append(vec[idx_depth])
        grouped_branch_ratio[tkey].append(vec[idx_branch])
        grouped_class[tkey].append(vec[idx_class])
        grouped_function[tkey].append(vec[idx_function])
        grouped_lambda[tkey].append(vec[idx_lambda])
        grouped_yield[tkey].append(vec[idx_yield])
        grouped_recursion[tkey].append(vec[idx_recursion])
        grouped_inheritance[tkey].append(vec[idx_inheritance])
        grouped_import[tkey].append(vec[idx_import])
        if fp.get("error"):
            parse_error_count += 1

    temp_keys = sorted(grouped.keys(), key=sort_key_temp)
    per_temperature = {}
    svi_groups = []

    for t in temp_keys:
        vectors = grouped[t]
        ctr = vector_mean(vectors)
        dists = []
        for vec in vectors:
            dist = euclidean_distance(vec, ctr)
            if dist is not None:
                dists.append(dist)

        svi = (sum(dists) / len(dists)) if dists else 0.0
        ci_low, ci_high = bootstrap_ci(dists, n_resamples=1000, seed=42)
        depth_var = variance(grouped_depth[t])
        branch_var = variance(grouped_branch_ratio[t])

        per_temperature[t] = {
            "centroid_vector": [round3(x) for x in ctr],
            "svi": round3(svi),
            "svi_bootstrap_ci_95": {"low": round3(ci_low), "high": round3(ci_high)},
            "ast_max_depth_variance": round3(depth_var),
            "branch_node_ratio_variance": round3(branch_var),
            "class_count": round3(sum(grouped_class[t]) / len(grouped_class[t])) if grouped_class[t] else 0.0,
            "function_count": round3(sum(grouped_function[t]) / len(grouped_function[t])) if grouped_function[t] else 0.0,
            "lambda_count": round3(sum(grouped_lambda[t]) / len(grouped_lambda[t])) if grouped_lambda[t] else 0.0,
            "yield_count": round3(sum(grouped_yield[t]) / len(grouped_yield[t])) if grouped_yield[t] else 0.0,
            "recursion_rate": round3(sum(grouped_recursion[t]) / len(grouped_recursion[t])) if grouped_recursion[t] else 0.0,
            "inheritance_edges": round3(sum(grouped_inheritance[t]) / len(grouped_inheritance[t])) if grouped_inheritance[t] else 0.0,
            "import_count": round3(sum(grouped_import[t]) / len(grouped_import[t])) if grouped_import[t] else 0.0,
            "valid_runs": len(vectors),
        }
        svi_groups.append(dists)

    anova = anova_one_way(svi_groups)
    ss_total = None
    eta_squared = None
    if anova.get("ss_between") is not None and anova.get("ss_within") is not None:
        ss_total = anova["ss_between"] + anova["ss_within"]
        if ss_total > 0:
            eta_squared = anova["ss_between"] / ss_total

    levene_input = []
    for g in svi_groups:
        if not g:
            continue
        m = sum(g) / len(g)
        levene_input.append([abs(x - m) for x in g])
    levene = anova_one_way(levene_input)

    out = {
        "input": input_path,
        "temperature_order": temp_keys,
        "feature_order": feature_names,
        "parse_error_count": parse_error_count,
        "per_temperature": per_temperature,
        "comparisons": {
            "anova_svi": {
                "f_stat": round3(anova.get("f_stat")),
                "ss_between": round3(anova.get("ss_between")),
                "ss_within": round3(anova.get("ss_within")),
                "df_between": anova.get("df_between"),
                "df_within": anova.get("df_within"),
            },
            "eta_squared": round3(eta_squared),
            "levene_style_svi": {
                "f_stat": round3(levene.get("f_stat")),
                "ss_between": round3(levene.get("ss_between")),
                "ss_within": round3(levene.get("ss_within")),
                "df_between": levene.get("df_between"),
                "df_within": levene.get("df_within"),
            },
        },
    }

    os.makedirs(runtime_dir, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(build_structural_report(temp_keys, per_temperature, out["comparisons"]))

    print(f"wrote {output_json}")
    print(f"wrote {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
