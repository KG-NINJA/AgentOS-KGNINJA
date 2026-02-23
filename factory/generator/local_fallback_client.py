#!/usr/bin/env python3
"""Pure local completion client for OpenAI-compatible endpoints (stdlib only)."""

import argparse
import http.client
import json
import os
import sys
import urllib.parse


FORBIDDEN = ["thinking", "exec", "$ ", "bash ", "sudo "]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Local completion fallback client")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--project-dir", required=True)
    p.add_argument("--model", default=os.environ.get("LOCAL_FALLBACK_MODEL", "qwen2.5-coder:7b"))
    p.add_argument(
        "--endpoint",
        default=os.environ.get("LOCAL_FALLBACK_ENDPOINT", "http://localhost:11434/v1/chat/completions"),
    )
    p.add_argument("--api-key", default=os.environ.get("LOCAL_FALLBACK_API_KEY", "ollama"))
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--response-json", default="runtime/local_completion_raw_response.json")
    return p.parse_args()


def normalize_endpoint(endpoint: str) -> urllib.parse.ParseResult:
    if "://" not in endpoint:
        endpoint = "http://" + endpoint
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.path:
        parsed = parsed._replace(path="/v1/chat/completions")
    return parsed


def request_completion(parsed: urllib.parse.ParseResult, payload: dict, api_key: str, timeout: int) -> dict:
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
    body = json.dumps(payload).encode("utf-8")
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", errors="replace")
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(f"HTTP {resp.status}: {raw[:400]}")
    return json.loads(raw)


def extract_content(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def validate_files_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON must be an object")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("missing or invalid 'files' object")
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            raise ValueError("file path keys must be non-empty strings")
        norm = os.path.normpath(rel_path)
        if os.path.isabs(norm) or norm.startswith("..") or "/../" in f"/{norm}/":
            raise ValueError(f"unsafe path: {rel_path}")
        if not isinstance(content, str):
            raise ValueError(f"content for '{rel_path}' must be string")
    return files


def contains_forbidden_text(text: str) -> str:
    lowered = text.lower()
    for token in FORBIDDEN:
        if token in lowered:
            return token
    return ""


def main() -> int:
    args = parse_args()

    try:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    except Exception as e:
        print(f"GENERATION_FORMAT_ERROR: cannot read prompt file: {e}", file=sys.stderr)
        return 2

    parsed = normalize_endpoint(args.endpoint)
    req_payload = {
        "model": args.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object: {\"files\":{\"relative/path\":\"content\"}}. "
                    "No markdown, no explanations, no tool usage, no shell commands."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": False,
        "tools": [],
        "response_format": {"type": "json_object"},
    }

    try:
        response = request_completion(parsed, req_payload, args.api_key, args.timeout)
    except Exception:
        print("LOCAL_COMPLETION_UNAVAILABLE", file=sys.stderr)
        return 3

    os.makedirs(os.path.dirname(os.path.abspath(args.response_json)), exist_ok=True)
    with open(args.response_json, "w", encoding="utf-8") as f:
        json.dump(response, f, ensure_ascii=False, indent=2)
        f.write("\n")

    content = extract_content(response).strip()
    if not content:
        print("GENERATION_FORMAT_ERROR: empty completion content", file=sys.stderr)
        return 2

    bad = contains_forbidden_text(content)
    if bad:
        print(f"GENERATION_FORMAT_ERROR: forbidden token '{bad}'", file=sys.stderr)
        return 2

    try:
        generated = json.loads(content)
    except Exception as e:
        print(f"GENERATION_FORMAT_ERROR: invalid JSON content ({e})", file=sys.stderr)
        return 2

    try:
        files = validate_files_payload(generated)
    except ValueError as e:
        print(f"GENERATION_FORMAT_ERROR: {e}", file=sys.stderr)
        return 2

    for rel_path, file_content in files.items():
        full_path = os.path.join(args.project_dir, os.path.normpath(rel_path))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(file_content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
