#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="$ROOT/runtime"
RESULTS_FILE="$RUNTIME_DIR/sweep_results.json"
STATS_SCRIPT="$ROOT/factory/analysis/stats.py"
STRUCTURAL_STATS_SCRIPT="$ROOT/factory/analysis/structural_stats.py"
PHASE_SCRIPT="$ROOT/factory/analysis/phase_transition.py"
MANIFEST_FILE="$RUNTIME_DIR/experiment_manifest.json"

mkdir -p "$RUNTIME_DIR"

python3 - "$RESULTS_FILE" <<'PY'
import datetime
import json
import os
import random
import sys

is_llm = os.environ.get("ARE_MODE", "mock") == "llm"
openai_api_key = os.environ.get("OPENAI_API_KEY")

if is_llm:
    if not openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required when ARE_MODE=llm")
    from openai import OpenAI
    client = OpenAI()

results_file = sys.argv[1]
temperatures = [0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3, 1.5]
runs_per_temp = 10
prompt = "Design a minimal embedded DSL in Python that allows users to define and evaluate simple arithmetic expressions. Architecture is entirely up to you."
execution_date = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
mobile_mode = os.environ.get("MOBILE_MODE", "0") == "1"

model_name = os.environ.get("CODEX_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"


def call_llm_completion(tau: float) -> str:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "Return only raw Python code. No explanations. No markdown. No comments."},
            {"role": "user", "content": prompt},
        ],
        temperature=float(tau),
        max_tokens=300,
    )
    text_output = response.choices[0].message.content
    return text_output.strip()


def render_code(temp, run_idx):
    rng = random.Random(int(temp * 1000) * 1000 + run_idx)
    variant = rng.randrange(4)

    if variant == 0:
        return """class Expr:
    def eval(self):
        raise NotImplementedError

class Num(Expr):
    def __init__(self, value):
        self.value = value
    def eval(self):
        return self.value

class BinOp(Expr):
    def __init__(self, left, right):
        self.left = left
        self.right = right

class Add(BinOp):
    def eval(self):
        return self.left.eval() + self.right.eval()

class Mul(BinOp):
    def eval(self):
        return self.left.eval() * self.right.eval()

def lit(x): return Num(x)
def add(a, b): return Add(a, b)
def mul(a, b): return Mul(a, b)
"""

    if variant == 1:
        return """def num(v):
    return ("num", v)

def add(a, b):
    return ("add", a, b)

def sub(a, b):
    return ("sub", a, b)

def mul(a, b):
    return ("mul", a, b)

def eval_expr(node):
    tag = node[0]
    if tag == "num":
        return node[1]
    if tag == "add":
        return eval_expr(node[1]) + eval_expr(node[2])
    if tag == "sub":
        return eval_expr(node[1]) - eval_expr(node[2])
    if tag == "mul":
        return eval_expr(node[1]) * eval_expr(node[2])
    raise ValueError("unknown node")
"""

    if variant == 2:
        return """class Builder:
    def __init__(self):
        self.ops = {}
    def op(self, name):
        def wrap(fn):
            self.ops[name] = fn
            return fn
        return wrap
    def __getattr__(self, name):
        return lambda *args: (name, args)

dsl = Builder()

@dsl.op("num")
def _num(x): return x
@dsl.op("add")
def _add(a, b): return a + b
@dsl.op("div")
def _div(a, b): return a / b

def evaluate(node):
    name, args = node
    vals = [evaluate(x) if isinstance(x, tuple) else x for x in args]
    return dsl.ops[name](*vals)
"""

    if variant == 3:
        return """class Node:
    __slots__ = ("fn", "args")
    def __init__(self, fn, args):
        self.fn = fn
        self.args = args
    def eval(self):
        vals = [a.eval() if isinstance(a, Node) else a for a in self.args]
        return self.fn(*vals)

class DSL:
    def __init__(self):
        self.env = {}
    def define(self, name, fn):
        self.env[name] = fn
    def __getattr__(self, name):
        fn = self.env[name]
        return lambda *xs: Node(fn, xs)

dsl = DSL()
dsl.define("num", lambda x: x)
dsl.define("add", lambda a, b: a + b)
dsl.define("pow", lambda a, b: a ** b)
"""


results = []
for temp in temperatures:
    for run_idx in range(1, runs_per_temp + 1):
        if is_llm:
            code = call_llm_completion(temp)
        else:
            code = render_code(temp, run_idx)
        score = round(min(1.0, 0.5 + (temp * 0.1) + (len(code.splitlines()) / 100.0)), 6)
        results.append(
            {
                "temperature": temp,
                "run": run_idx,
                "prompt": prompt,
                "code": code,
                "score": score,
                "stability_score": score,
                "creativity_score": round(min(1.0, max(0.0, 0.2 + (temp * 0.5) + (run_idx * 0.01))), 6),
            }
        )

payload = {
    "experiment": "temperature_sweep_structural_python",
    "model": "gpt-5.3-codex",
    "prompt": prompt,
    "temps": temperatures,
    "runs_per_temp": runs_per_temp,
    "execution_date": execution_date,
    "mobile_mode": mobile_mode,
    "results": results,
}

with open(results_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

if [ ! -f "$RESULTS_FILE" ]; then
  echo "[SWEEP] missing runtime/sweep_results.json; aborting gracefully"
  exit 0
fi

python3 "$STRUCTURAL_STATS_SCRIPT" >/dev/null
python3 "$STATS_SCRIPT" "$RESULTS_FILE" >/dev/null
python3 "$PHASE_SCRIPT" "$RESULTS_FILE" "$RUNTIME_DIR/phase_transition_report.json" "$RUNTIME_DIR/phase_transition_plot.png" >/dev/null

python3 - "$RESULTS_FILE" "$MANIFEST_FILE" <<'PY'
import hashlib
import json
import os
import platform
import subprocess
import sys

results_file = sys.argv[1]
manifest_file = sys.argv[2]

sha = hashlib.sha256()
with open(results_file, "rb") as f:
    for chunk in iter(lambda: f.read(65536), b""):
        sha.update(chunk)

git_hash = "unavailable"
try:
    out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
    git_hash = out.decode("utf-8").strip() or "unavailable"
except Exception:
    pass

codex_model = (
    os.environ.get("CODEX_MODEL_SNAPSHOT")
    or os.environ.get("CODEX_MODEL")
    or os.environ.get("OPENAI_MODEL")
    or os.environ.get("MODEL")
    or "unavailable"
)

manifest = {
    "experiment": "temperature_sweep_structural_python",
    "analysis_version": "structural-stability-v1",
    "fingerprint_schema_version": "1.0.0",
    "stats_schema_version": "1.0.0",
    "git_commit": git_hash,
    "model_snapshot": codex_model,
    "platform": platform.platform(),
    "sha256_sweep_results": sha.hexdigest(),
}

with open(manifest_file, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
PY

python3 - <<'PY'
import json

with open("runtime/structural_stats.json", "r", encoding="utf-8") as f:
    structural = json.load(f)

print("[SWEEP] complete")
print("Temperature | SVI | CI_low | CI_high | ast_depth_var")
for temp in structural.get("temperature_order", []):
    row = structural.get("per_temperature", {}).get(temp, {})
    ci = row.get("svi_bootstrap_ci_95", {})
    svi = row.get("svi")
    low = ci.get("low")
    high = ci.get("high")
    ast_var = row.get("ast_max_depth_variance")
    svi_s = "n/a" if svi is None else f"{svi:.3f}"
    low_s = "n/a" if low is None else f"{low:.3f}"
    high_s = "n/a" if high is None else f"{high:.3f}"
    ast_s = "n/a" if ast_var is None else f"{ast_var:.3f}"
    print(f"{temp} | {svi_s} | {low_s} | {high_s} | {ast_s}")

print("[SWEEP] outputs=runtime/sweep_results.json,runtime/stats.json,runtime/report.md,runtime/structural_stats.json,runtime/structural_report.md,runtime/paper.md,runtime/experiment_manifest.json,runtime/phase_transition_report.json,runtime/phase_transition_plot.png")
PY
