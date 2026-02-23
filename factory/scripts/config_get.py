#!/usr/bin/env python3
"""Lookup utility for factory config values."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: config_get.py <key> <default>")

    key = sys.argv[1]
    default = sys.argv[2]

    script_path = Path(__file__).resolve()
    candidates = [
        script_path.parents[2] / "config.json",  # repo root
        script_path.parents[1] / "config.json",  # legacy factory/config.json
    ]

    config_path = None
    for candidate in candidates:
        if candidate.exists():
            config_path = candidate
            break
    if config_path is None:
        print(default)
        return 0

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(default)
        return 0

    value = cfg.get(key, default)
    if isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
