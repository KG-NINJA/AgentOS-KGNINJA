# Pure Local Fallback (Agent-Free)

## Why Codex CLI proxy is unsuitable

Codex CLI may emit agent-style content (e.g. "thinking", "exec"), which breaks deterministic harness parsing and JSON-only artifact generation.

## Why a pure completion server is required

The harness needs a strict completion response that can be validated as a single JSON object:

```json
{
  "files": {
    "core/server.js": "..."
  }
}
```

No tool calls, no agent actions, no execution directives.

## Start Ollama local completion server

1. Install Ollama locally.
2. Start server:

```bash
ollama serve
```

3. Pull a local code model once:

```bash
ollama pull qwen2.5-coder:7b
```

4. Verify setup helper:

```bash
bash factory/generator/local_completion_setup.sh
```

Endpoint used by fallback:

- `http://localhost:11434/v1/chat/completions`

## Environment configuration

```bash
export LOCAL_FALLBACK_ENDPOINT="http://localhost:11434/v1/chat/completions"
export LOCAL_FALLBACK_MODEL="qwen2.5-coder:7b"
export LOCAL_FALLBACK_API_KEY="ollama"
```

## Test local fallback directly

```bash
mkdir -p runtime workspace
cat > runtime/local_fallback_prompt.txt <<'TXT'
Return JSON only with files for a minimal web app.
TXT

python3 factory/generator/local_fallback_client.py \
  --prompt-file runtime/local_fallback_prompt.txt \
  --project-dir workspace/project-900 \
  --endpoint "${LOCAL_FALLBACK_ENDPOINT:-http://localhost:11434/v1/chat/completions}" \
  --model "${LOCAL_FALLBACK_MODEL:-qwen2.5-coder:7b}"
```

## Example curl test

```bash
curl -sS http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen2.5-coder:7b",
    "messages":[{"role":"user","content":"Return only JSON: {\"files\":{\"core/server.js\":\"console.log(1)\"}}"}],
    "stream":false,
    "temperature":0,
    "max_tokens":256,
    "tools":[],
    "response_format":{"type":"json_object"}
  }'
```

## Diagnostic run command

```bash
bash diagnostics/diagnose_generator_runs.sh 5
python3 diagnostics/parser_generator_logs.py --activity-log runtime/activity.log --output diagnostics/output/generator_log_parse.json
```
