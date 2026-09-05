#!/usr/bin/env python3
"""Run a synthetic read DAG and context compiler; zero external requests."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time

from work_kernel import EvidenceStore, ReadOperation, Rejected, compile_context, read_scoped, run_reads


async def smoke():
    with tempfile.TemporaryDirectory(prefix="gpt6-work-smoke-") as directory:
        root = Path(directory)
        (root / "policy.md").write_text("Read only. No economic authority.\n")
        (root / "state.md").write_text("Synthetic test data; not a provider response.\n")
        def validate(args):
            if set(args) != {"path"} or args["path"] not in ("policy.md", "state.md"):
                raise Rejected("unapproved file")
        async def read(args, deps):
            raw = read_scoped(root, args["path"])
            return {"data": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
                    "source_refs": ["fixture:" + args["path"]], "observed_at": time.time(), "warnings": []}
        registry = {"local.read": ReadOperation("v1", validate, read)}
        tasks = [{"id": name, "operation": "local.read", "args": {"path": name + ".md"}, "depends_on": []}
                 for name in ("policy", "state")]
        store = EvidenceStore(root / "evidence.db")
        try:
            first = await run_reads("synthetic-smoke", tasks, registry, ("local.read",), store)
            second = await run_reads("synthetic-smoke", tasks, registry, ("local.read",), store)
            context = compile_context(root, [{"path": "policy.md", "required": True}, {"path": "state.md", "required": True}])
            assert first == second and first["status"] == "ok"
            return {"status": "passed", "synthetic": True, "external_requests": 0,
                    "read_tasks": len(first["events"]), "context_sources": len(context["sources"]),
                    "replay_identical": first == second, "model_access_verified": False,
                    "live_deployment_activated": False}
        finally:
            store.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), sort_keys=True))
