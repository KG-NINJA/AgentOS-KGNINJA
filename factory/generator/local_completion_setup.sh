#!/usr/bin/env bash
# Prepare local pure completion server (Ollama) for fallback usage.
set -euo pipefail

MODEL="${LOCAL_FALLBACK_MODEL:-qwen2.5-coder:7b}"
ENDPOINT="http://localhost:11434/v1/chat/completions"

if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama is not installed." >&2
  echo "Install Ollama first: https://ollama.com/download" >&2
  echo "Then run: ollama serve" >&2
  echo "Then pull model manually (one-time): ollama pull ${MODEL}" >&2
  exit 1
fi

if ! curl -sS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Starting local ollama server..."
  nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
  sleep 2
fi

if ! curl -sS --max-time 3 http://localhost:11434/api/tags | grep -Fq "\"name\":\"${MODEL}\""; then
  echo "Model not found locally: ${MODEL}" >&2
  echo "Pull it manually (requires network once): ollama pull ${MODEL}" >&2
  echo "No automatic pull performed by harness." >&2
  exit 1
fi

echo "Local completion server ready."
echo "Model: ${MODEL}"
echo "Endpoint: ${ENDPOINT}"
