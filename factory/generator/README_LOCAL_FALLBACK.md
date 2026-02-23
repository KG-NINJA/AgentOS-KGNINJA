# Local Fallback Generator

This project includes an offline local fallback generator for `factory/generator/codex_generate.sh`.

When primary cloud generation fails, it invokes:

- `factory/generator/local_fallback.sh`
- `factory/generator/local_fallback_client.py` (Python stdlib only)

## Supported local model servers

Any OpenAI-compatible local endpoint works. Common options:

- Ollama OpenAI-compatible API
- vLLM OpenAI-compatible server
- Other local servers exposing `/v1/chat/completions`

## Configuration

Environment variables:

- `FACTORY_LOCAL_LLM_ENDPOINT` (default: `http://127.0.0.1:11434/v1/chat/completions`)
- `FACTORY_LOCAL_LLM_MODEL` (default: `qwen2.5-coder:7b`)
- `FACTORY_LOCAL_LLM_API_KEY` (default: `local`)
- `FACTORY_LOCAL_LLM_TIMEOUT_SECONDS` (default: `30`)
- `FACTORY_LOCAL_LLM_AUTOSTART` (default: `0`)
- `FACTORY_LOCAL_LLM_START_CMD` (optional; used only when AUTOSTART=1)
- `FACTORY_ALLOW_CODEX_FALLBACK` (default: `1`)

## Example: Ollama (offline model already pulled)

Start local server:

```bash
ollama serve
```

Run with local fallback settings:

```bash
export FACTORY_LOCAL_LLM_ENDPOINT="http://127.0.0.1:11434/v1/chat/completions"
export FACTORY_LOCAL_LLM_MODEL="qwen2.5-coder:7b"
./factory.sh run
```

## Example: vLLM

Serve locally:

```bash
python3 -m vllm.entrypoints.openai.api_server --model /path/to/local/checkpoint --host 127.0.0.1 --port 8000
```

Configure and run:

```bash
export FACTORY_LOCAL_LLM_ENDPOINT="http://127.0.0.1:8000/v1/chat/completions"
export FACTORY_LOCAL_LLM_MODEL="/path/to/local/checkpoint"
./factory.sh run
```

## Test local fallback independently

Prepare a spec and project path, then run:

```bash
mkdir -p runtime workspace
cat > runtime/spec.json <<'JSON'
{"project_type":"web_app","ai_task":"local fallback test"}
JSON

bash factory/generator/local_fallback.sh --project-dir workspace/project-999 --spec-file runtime/spec.json
```

Check outputs:

- `workspace/project-999/core/server.js`
- `workspace/project-999/core/public/app.js`
- `workspace/project-999/core/package.json`
- `workspace/project-999/docs/LOCAL_FALLBACK_RESPONSE.md`

## Logs

`runtime/activity.log` markers:

- `GENERATOR_FALLBACK_INVOCATION=local_fallback`
- `GENERATOR_FALLBACK_MODEL=...`
- `GENERATOR_FALLBACK_RC=...`
- `GENERATOR_FINAL_EXIT_SOURCE=local_fallback_success|local_fallback_failure`

stderr capture:

- `runtime/generator_stderr.log`

## Diagnostics

Parse generator markers:

```bash
python3 diagnostics/parser_generator_logs.py --activity-log runtime/activity.log --output diagnostics/output/generator_log_parse.json
```

Run multi-run diagnostics:

```bash
bash diagnostics/diagnose_generator_runs.sh 5
```
