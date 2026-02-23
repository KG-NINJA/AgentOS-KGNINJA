#!/usr/bin/env bash
set -euo pipefail

# Rule table columns:
# category|phases|exit_codes|weight|signature_patterns(; separated)
# phases: * or comma-separated list
# exit_codes: * or comma-separated list
RULES_FILE_DEFAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/classification_rules.tsv"

json_escape() {
  local s="${1:-}"
  s=${s//\\/\\\\}
  s=${s//"/\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

load_rules() {
  local rules_file="$1"
  if [ ! -f "$rules_file" ]; then
    echo '{"error":"RULES_FILE_NOT_FOUND"}'
    exit 1
  fi
  mapfile -t RULE_LINES < <(grep -Ev '^[[:space:]]*#|^[[:space:]]*$' "$rules_file")
}

phase_match() {
  local phase="$1"
  local phases_csv="$2"
  if [ "$phases_csv" = "*" ]; then
    return 0
  fi
  local norm_phase norm_phases
  norm_phase="$(printf '%s' "$phase" | tr '[:lower:]' '[:upper:]')"
  norm_phases=",$(printf '%s' "$phases_csv" | tr '[:lower:]' '[:upper:]'),"
  [[ "$norm_phases" == *",$norm_phase,"* ]]
}

exit_match() {
  local code="$1"
  local exits_csv="$2"
  if [ "$exits_csv" = "*" ]; then
    return 0
  fi
  local norm
  norm=",$exits_csv,"
  [[ "$norm" == *",$code,"* ]]
}

count_signature_matches() {
  local text="$1"
  local patterns_csv="$2"
  local -n _out_matches_ref=$3
  local count=0
  _out_matches_ref=()

  IFS=';' read -r -a patterns <<< "$patterns_csv"
  for p in "${patterns[@]}"; do
    [ -z "$p" ] && continue
    if printf '%s' "$text" | grep -E -q "$p"; then
      _out_matches_ref+=("$p")
      count=$((count + 1))
    fi
  done

  printf '%s' "$count"
}

compute_confidence() {
  local signature_score="$1"
  local exit_score="$2"
  local weight="$3"
  local raw_score max_score

  raw_score=$(( (signature_score * 2) + exit_score ))
  max_score=$(( (weight * 2) + 1 ))

  awk -v r="$raw_score" -v m="$max_score" 'BEGIN { c=(m>0)?(r/m):0; if (c>1) c=1; if (c<0) c=0; printf "%.4f", c }'
}

render_json() {
  local phase="$1"
  local exit_code="$2"
  local category="$3"
  local confidence="$4"
  local exit_code_match="$5"
  local rule_weight="$6"
  local total_weight="$7"
  shift 7
  local matches=("$@")

  local matches_json=""
  if [ "${#matches[@]}" -eq 0 ]; then
    matches_json=""
  else
    local i
    for i in "${!matches[@]}"; do
      [ "$i" -gt 0 ] && matches_json+=","
      matches_json+="\"$(json_escape "${matches[$i]}")\""
    done
  fi

  printf '{'
  printf '"phase":"%s",' "$(json_escape "$phase")"
  printf '"exit_code":%s,' "$exit_code"
  printf '"category":"%s",' "$(json_escape "$category")"
  printf '"confidence":%s,' "$confidence"
  printf '"signals":{'
  printf '"signature_matches":[%s],' "$matches_json"
  printf '"exit_code_match":%s,' "$exit_code_match"
  printf '"rule_weight":%s,' "$rule_weight"
  printf '"total_weight":%s' "$total_weight"
  printf '}'
  printf '}'
  printf '\n'
}

classify() {
  local phase="$1"
  local exit_code="$2"
  local text="$3"

  # deterministic success short-circuit
  if [ "$exit_code" -eq 0 ]; then
    render_json "$phase" "$exit_code" "SUCCESS" "1.0000" "true" 1 1
    return 0
  fi

  local best_category="UNKNOWN_NONZERO"
  local best_confidence="0.0000"
  local best_exit_match="false"
  local best_weight=1
  local best_matches=()

  local line category phases exits weight patterns
  for line in "${RULE_LINES[@]}"; do
    IFS='|' read -r category phases exits weight patterns <<< "$line"

    phase_match "$phase" "$phases" || continue

    local matched_patterns=()
    local signature_score exit_score exit_ok confidence
    signature_score="$(count_signature_matches "$text" "$patterns" matched_patterns)"

    if exit_match "$exit_code" "$exits"; then
      exit_score=1
      exit_ok="true"
    else
      exit_score=0
      exit_ok="false"
    fi

    # HARD_POLICY_BLOCK requires explicit signature, never by exit code only
    if [ "$category" = "HARD_POLICY_BLOCK" ] && [ "$signature_score" -eq 0 ]; then
      confidence="0.0000"
    else
      confidence="$(compute_confidence "$signature_score" "$exit_score" "$weight")"
    fi

    # rule can trigger only if there is at least one signal
    if [ "$signature_score" -eq 0 ] && [ "$exit_score" -eq 0 ]; then
      continue
    fi

    # deterministic tie-breakers: higher confidence -> higher weight -> lexical category
    local better=0
    if awk -v a="$confidence" -v b="$best_confidence" 'BEGIN{exit !(a>b)}'; then
      better=1
    elif awk -v a="$confidence" -v b="$best_confidence" 'BEGIN{exit !(a==b)}'; then
      if [ "$weight" -gt "$best_weight" ]; then
        better=1
      elif [ "$weight" -eq "$best_weight" ] && [[ "$category" < "$best_category" ]]; then
        better=1
      fi
    fi

    if [ "$better" -eq 1 ]; then
      best_category="$category"
      best_confidence="$confidence"
      best_exit_match="$exit_ok"
      best_weight="$weight"
      best_matches=("${matched_patterns[@]}")
    fi
  done

  # fallback: unknown non-zero, deterministic confidence from exit match only
  if [ "$best_category" = "UNKNOWN_NONZERO" ] && [ "${#best_matches[@]}" -eq 0 ]; then
    local fallback_conf
    fallback_conf="$(compute_confidence 0 1 1)"
    render_json "$phase" "$exit_code" "UNKNOWN_NONZERO" "$fallback_conf" "true" 1 1
  else
    render_json "$phase" "$exit_code" "$best_category" "$best_confidence" "$best_exit_match" "$best_weight" "$best_weight" "${best_matches[@]}"
  fi
}

usage() {
  cat <<'USAGE'
Usage:
  classification.sh --phase <PHASE> --exit-code <INT> [--stderr-file <PATH>] [--text <STRING>] [--rules-file <PATH>]
USAGE
}

main() {
  local phase="" exit_code="" stderr_file="" text_input="" rules_file="$RULES_FILE_DEFAULT"

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --phase)
        phase="$2"; shift 2 ;;
      --exit-code)
        exit_code="$2"; shift 2 ;;
      --stderr-file)
        stderr_file="$2"; shift 2 ;;
      --text)
        text_input="$2"; shift 2 ;;
      --rules-file)
        rules_file="$2"; shift 2 ;;
      --help|-h)
        usage; exit 0 ;;
      *)
        echo '{"error":"INVALID_ARGUMENT"}'
        exit 1 ;;
    esac
  done

  if [ -z "$phase" ] || [ -z "$exit_code" ]; then
    echo '{"error":"MISSING_REQUIRED_ARGUMENTS"}'
    exit 1
  fi

  local text="$text_input"
  if [ -n "$stderr_file" ] && [ -f "$stderr_file" ]; then
    text="${text}$(cat "$stderr_file")"
  fi

  load_rules "$rules_file"
  classify "$phase" "$exit_code" "$text"
}

main "$@"
