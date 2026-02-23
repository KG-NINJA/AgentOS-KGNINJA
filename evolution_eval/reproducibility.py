#!/usr/bin/env python3
"""Reproducibility artifact helpers for evolution_eval."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def compute_file_hash(path: str) -> str:
    """SHA256 hash of file bytes."""
    p = Path(path)
    return _sha256_bytes(p.read_bytes())


def compute_config_hash(config_dict: Dict[str, Any]) -> str:
    """SHA256 hash of canonical JSON config representation."""
    payload = json.dumps(config_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(payload)


def compute_code_hash(module_list: Iterable[str]) -> str:
    """SHA256 hash over concatenated per-file hashes in sorted path order."""
    parts: List[str] = []
    for module_path in sorted(str(p) for p in module_list):
        parts.append(f"{module_path}:{compute_file_hash(module_path)}")
    return _sha256_bytes("\n".join(parts).encode("utf-8"))


def build_repro_report(
    *,
    config: Dict[str, Any],
    input_files: Iterable[str],
    module_files: Iterable[str],
    seed: int,
    sweep_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build deterministic reproducibility report payload.

    No timestamps are included to keep outputs reproducible.
    """
    input_map = {str(p): compute_file_hash(str(p)) for p in sorted(str(x) for x in input_files)}
    input_hash = _sha256_bytes(
        "\n".join(f"{k}:{v}" for k, v in sorted(input_map.items())).encode("utf-8")
    )

    sweep_hash = None
    if sweep_payload is not None:
        sweep_hash = _sha256_bytes(
            json.dumps(sweep_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )

    return {
        "config_hash": compute_config_hash(config),
        "code_hash": compute_code_hash(module_files),
        "input_hash": input_hash,
        "input_files": input_map,
        "sweep_hash": sweep_hash,
        "seed": int(seed),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }
