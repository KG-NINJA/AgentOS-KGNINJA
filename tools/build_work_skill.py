#!/usr/bin/env python3
"""Build a reproducible Skill ZIP. Does not install or activate ChatGPT Skills."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".agents/skills/gpt6-work-platform"


def build(output: Path) -> dict:
    payload = {}
    for path in sorted(SOURCE.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are not package inputs")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix not in (".md", ".py", ".json"):
            continue
        payload[path.relative_to(SOURCE).as_posix()] = path.read_bytes()
    if "SKILL.md" not in payload:
        raise ValueError("missing SKILL.md")
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    payload["PACKAGE_SHA256.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    if output.is_symlink() or output.resolve().is_relative_to(SOURCE):
        raise ValueError("unsafe package destination")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently replacing an operator's file.
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo("gpt6-work-platform/" + name, date_time=(2026, 9, 5, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    return {"files": len(payload), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "installed": False, "model_activated": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), sort_keys=True))
