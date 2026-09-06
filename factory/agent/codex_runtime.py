"""Host-owned Codex CLI routing; no API client or credential management.

The candidate is opt-in until paired evaluation and deployment review pass.
Environment is operator configuration, never copied from a task or model output.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping

ROLES = ("generation", "repair", "interpretation")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def selection(environ: Mapping[str, str] | None = None) -> dict[str, str | None]:
    env = os.environ if environ is None else environ
    profile = env.get("FACTORY_CODEX_PROFILE", "legacy")
    if profile not in ("legacy", "gpt6"):
        raise ValueError("unsupported FACTORY_CODEX_PROFILE")
    effort = env.get("FACTORY_CODEX_EFFORT")
    if profile == "gpt6" and effort not in EFFORTS:
        raise ValueError("GPT-6 requires explicit FACTORY_CODEX_EFFORT: " + ", ".join(EFFORTS))
    if profile == "legacy" and effort is not None:
        raise ValueError("FACTORY_CODEX_EFFORT requires the gpt6 profile")
    return {"profile": profile, "model": "gpt-6-astra" if profile == "gpt6" else None,
            "effort": effort}


def command(role: str, args: list[str], environ: Mapping[str, str] | None = None) -> list[str]:
    if role not in ROLES:
        raise ValueError("unsupported Codex role")
    selected = selection(environ)
    if selected["profile"] == "legacy":
        # Preserve the generator pin and inherited repair/parser settings.
        options = ["--model", "gpt-5.3-codex"] if role == "generation" else []
    else:
        options = ["--model", "gpt-6-astra", "-c",
                   'model_reasoning_effort=' + json.dumps(selected["effort"])]
    return ["codex", *options, *args]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--inspect", action="store_true", help="Print routing only; never call Codex")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args()
    try:
        selected = selection()
        if parsed.inspect:
            print(json.dumps({**selected, "role": parsed.role, "model_access_verified": False}))
            return 0
        args = parsed.args[1:] if parsed.args[:1] == ["--"] else parsed.args
        if not args:
            raise ValueError("Codex command is required")
        return subprocess.run(command(parsed.role, args), check=False).returncode
    except (ValueError, OSError):
        print("fail_reason=codex-runtime-config-or-launch-error", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
