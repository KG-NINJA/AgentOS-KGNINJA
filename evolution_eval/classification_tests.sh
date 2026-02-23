#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFIER="$SCRIPT_DIR/classification.sh"
RULES="$SCRIPT_DIR/classification_rules.tsv"

run_case() {
  local phase="$1" code="$2" text="$3"
  "$CLASSIFIER" --phase "$phase" --exit-code "$code" --text "$text" --rules-file "$RULES"
}

# 1) syntax error
run_case "GENERATOR" 2 "unexpected EOF while looking for matching quote"

# 2) dependency error
run_case "GENERATOR" 127 "command not found: jq"

# 3) explicit hard policy block
run_case "DECISION" 1 "[POLICY_BLOCK] HARD_POLICY_BLOCK_SIGNATURE limit reached"

# 4) timeout
run_case "GENERATOR" 124 "operation timed out after 90s"

# 5) unknown non-zero
run_case "BRAIN" 1 "non-zero exit without known signature"

# 6) success
run_case "POST_GATE" 0 "all checks passed"
