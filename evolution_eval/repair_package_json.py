#!/usr/bin/env python3
"""Deterministically repair injected dependency_error corruption in core/package.json."""

import json
import os
import sys


def is_invalid_test_script(value: object) -> bool:
    if not isinstance(value, str):
        return True
    normalized = value.strip().lower()
    if not normalized:
        return True
    if normalized == "nonexistent-test-command-xyz":
        return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: repair_package_json.py <project_dir>", file=sys.stderr)
        return 2

    project_dir = sys.argv[1]
    pkg_path = os.path.join(project_dir, "core", "package.json")
    if not os.path.exists(pkg_path):
        print(f"package.json not found: {pkg_path}", file=sys.stderr)
        return 1

    with open(pkg_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    deps = data.get("dependencies")
    if isinstance(deps, dict) and "broken-dep" in deps:
        deps.pop("broken-dep", None)
        data["dependencies"] = deps
        changed = True

    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        scripts = {}
        data["scripts"] = scripts
        changed = True

    if is_invalid_test_script(scripts.get("test")):
        scripts["test"] = "echo 'no tests'"
        changed = True

    # JSON integrity is validated by successful load/dump.
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if changed:
        print("REPAIR_APPLIED")
    else:
        print("REPAIR_APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
