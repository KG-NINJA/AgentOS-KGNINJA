"""python3 -m factory.revenue.cli: collect -> persist -> prioritize -> internal brief."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import uuid
from .policy import POLICY
from .sources import RevenueError, SOURCES, collect_one, instant, stamp, strict_json
from .store import Store


def collect(store, source_keys, run_id, reader=None):
    if not store.begin(run_id, source_keys):
        return {"replayed": True, "run_id": run_id, "report": store.report()}
    now = instant(stamp())
    eligible = [key for key in source_keys if not store.cooldown(key, now)]
    skipped = [key for key in source_keys if key not in eligible]
    # Network reads only run concurrently; the host serializes all persistence.
    with ThreadPoolExecutor(max_workers=POLICY["max_parallel_reads"]) as executor:
        futures = [executor.submit(collect_one, SOURCES[key], reader) for key in eligible]
        for future in as_completed(futures):
            store.record(run_id, future.result())
    store.finish(run_id)
    return {"replayed": False, "run_id": run_id, "cooldown_sources": skipped, "report": store.report()}


def main(argv=None):
    effective = sys.argv[1:] if argv is None else argv
    if effective and effective[0] == "control":
        from .control.cli import main as control_main
        return control_main(effective[1:])
    parser = argparse.ArgumentParser(description="Observe existing revenue paths and prepare private follow-up work. No external writes.")
    parser.add_argument("--db", default="runtime/revenue/evidence.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("collect")
    scan.add_argument("--sources", nargs="+", choices=list(SOURCES), default=list(SOURCES))
    scan.add_argument("--run-id", default=None)
    imported = sub.add_parser("import-capture")
    imported.add_argument("manifest")
    imported.add_argument("--run-id", required=True)
    sub.add_parser("report")
    sub.add_parser("next")
    brief = sub.add_parser("brief")
    brief.add_argument("task_key")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    sub.add_parser("stop")
    args = parser.parse_args(argv)
    store = None
    try:
        store = Store(args.db)
        if args.command == "collect":
            output = collect(store, args.sources, args.run_id or str(uuid.uuid4()))
        elif args.command == "import-capture":
            path = Path(args.manifest)
            if path.is_symlink() or path.stat().st_size > 12_000_000:
                raise RevenueError("CAPTURE_FILE_UNSAFE_OR_TOO_LARGE")
            records = validate_capture(strict_json(path.read_bytes()))
            if store.begin(args.run_id, [r["source_key"] for r in records]):
                for record in records:
                    store.record(args.run_id, record)
                store.finish(args.run_id)
            else:
                # A completed import replay must still match exact bytes, timestamps and method.
                for record in records:
                    store.record(args.run_id, record)
            output = {"imported_source_claims": len(records), "report": store.report()}
        elif args.command in ("report", "next"):
            output = store.report()
            if args.command == "next":
                output = {"mode": output["mode"], "next_actions": [t for t in output["next_actions"] if t["state"] == "OPEN"][:5],
                          "waiting_or_prepared_count": sum(t["state"] != "OPEN" for t in output["next_actions"]),
                          "external_execution_authorized": False}
        elif args.command == "brief":
            output = store.prepare_brief(args.task_key)
        elif args.command == "backup":
            output = {"backup": store.backup(args.destination), "restored_new_work_stopped": True}
        else:
            store.stop()
            output = {"stopped": True, "read_and_reconciliation_available": True}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (RevenueError, OSError) as exc:
        code = str(exc) if isinstance(exc, RevenueError) else "LOCAL_IO_ERROR"
        print(json.dumps({"error": code, "external_execution_authorized": False}), file=sys.stderr)
        return 78
    finally:
        if store:
            store.close()


def validate_capture(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "captures"} or manifest["schema_version"] != "revenue-source-capture/0.1":
        raise RevenueError("INVALID_CAPTURE_MANIFEST")
    captures = manifest["captures"]
    if not isinstance(captures, list) or not 1 <= len(captures) <= len(SOURCES):
        raise RevenueError("INVALID_CAPTURE_COUNT")
    records = []
    for item in captures:
        if not isinstance(item, dict) or set(item) != {"source_key", "url", "fetched_at", "source_at", "raw_json"}:
            raise RevenueError("INVALID_CAPTURE_FIELDS")
        key = item["source_key"]
        if key not in SOURCES or item["url"] != SOURCES[key].url:
            raise RevenueError("SOURCE_NOT_ALLOWLISTED")
        if not isinstance(item["raw_json"], str):
            raise RevenueError("RAW_JSON_REQUIRED")
        instant(item["fetched_at"])
        data = strict_json(item["raw_json"])
        source_at = item["source_at"]
        if source_at is not None:
            instant(source_at)
        # A body publication timestamp takes precedence over the host capture timestamp.
        if isinstance(data, dict):
            source_at = data.get("generated_at", data.get("time", source_at))
        if source_at is not None:
            instant(source_at)
        if len(item["raw_json"].encode("utf-8")) > 6_100_000:
            raise RevenueError("CAPTURE_TOO_LARGE")
        records.append({"source_key": key, "fetched_at": item["fetched_at"], "source_at": source_at,
                        "raw": item["raw_json"].encode("utf-8"), "ok": True, "error": None,
                        "retry_after": None, "capture_method": "host_import"})
    if len({r["source_key"] for r in records}) != len(records):
        raise RevenueError("DUPLICATE_CAPTURE_SOURCE")
    return records


if __name__ == "__main__":
    raise SystemExit(main())
