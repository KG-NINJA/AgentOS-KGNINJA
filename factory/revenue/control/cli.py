"""Explicit host invocations; no installation, live payment or automatic login."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from .contracts import ControlError
from .database import Database
from .demo import run_demo
from .sandbox import DockerSandbox, git_snapshot
from .service import HostConfiguration, make_server
from ..sources import RevenueError, digest, json_bytes


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authenticated revenue workflow controller")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--config", required=True)
    serve.add_argument("--db", default="runtime/revenue/controller.sqlite3")
    serve.add_argument("--port", type=int, default=8789)
    sub.add_parser("capabilities")
    sub.add_parser("demo")
    backup = sub.add_parser("backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("destination")
    snapshot = sub.add_parser("snapshot-hash")
    snapshot.add_argument("repo")
    snapshot.add_argument("commit")
    invoke = sub.add_parser("invoke")
    invoke.add_argument("operation")
    invoke.add_argument("--role", required=True)
    invoke.add_argument("--payload", required=True)
    invoke.add_argument("--key", required=True)
    invoke.add_argument("--origin", default="http://127.0.0.1:8789")
    exported = sub.add_parser("export-job")
    exported.add_argument("job_id")
    exported.add_argument("--output", required=True)
    exported.add_argument("--key", required=True)
    exported.add_argument("--origin", default="http://127.0.0.1:8789")
    imported = sub.add_parser("import-result")
    imported.add_argument("--job-package", required=True)
    imported.add_argument("--result", required=True)
    imported.add_argument("--artifact", required=True)
    imported.add_argument("--key", required=True)
    imported.add_argument("--origin", default="http://127.0.0.1:8789")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            config = HostConfiguration(args.config)
            c = config.controller(args.db)
            c.close()
            server = make_server(args.db, config, args.port)
            print(json.dumps({"listening": "http://127.0.0.1:" + str(server.server_port), "owner": "/owner", "agent": "/agent", "payment_execution": False}), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        if args.command == "invoke":
            from .client import invoke, read_input
            output = invoke(args.origin, args.role, args.operation, read_input(args.payload), args.key)
        elif args.command == "export-job":
            from .client import export_claim
            output = export_claim(args.origin, args.job_id, args.key, args.output)
        elif args.command == "import-result":
            from .client import import_result
            output = import_result(args.origin, args.job_package, args.result, args.artifact, args.key)
        elif args.command == "capabilities":
            import shutil
            output = {"isolation": DockerSandbox().capabilities(), "codex_cli_available": bool(shutil.which("codex")),
                      "codex_billing_and_login_verified": False, "remote_installation_verified": False}
        elif args.command == "demo":
            with tempfile.TemporaryDirectory(prefix="kg-revenue-demo-") as directory:
                output = run_demo(Path(directory) / "synthetic.sqlite3", time.time())
        elif args.command == "snapshot-hash":
            files = git_snapshot(args.repo, args.commit)
            output = {"commit": args.commit, "snapshot_sha256": digest(json_bytes(files)), "files": len(files)}
        else:
            db = Database(args.db)
            try:
                output = {"backup": db.backup(args.destination), "restored_runtime_stopped": True}
            finally:
                db.close()
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ControlError, RevenueError, OSError) as exc:
        print(json.dumps({"error": exc.code if isinstance(exc, ControlError) else "HOST_INPUT_OR_CAPABILITY_UNAVAILABLE"}), file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
