"""Regressions reproduced against the original kernel; no external requests."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import work_kernel as k


class StrictInputTests(unittest.TestCase):
    def test_scoped_json_duplicate_fields_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.json").write_text('{"limit":0,"limit":999}')
            with self.assertRaises(k.Rejected):
                k.read_scoped(root, "sample.json")

    def test_noncanonical_source_aliases_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.md").write_text("data")
            for path in ("./sample.md", "././sample.md"):
                with self.subTest(path=path), self.assertRaises(k.Rejected):
                    k.read_scoped(root, path)

    def test_receipt_wrong_run_cannot_finalize(self):
        with tempfile.TemporaryDirectory() as d:
            store = k.EvidenceStore(Path(d) / "evidence.db")
            try:
                store.claim("real", "1" * 64)
                with self.assertRaises(k.Rejected):
                    store.finish("real", {"run_id": "other", "plan_hash": "1" * 64}, {})
                self.assertEqual(store.db.execute("SELECT state FROM runs").fetchone()[0], "running")
            finally:
                store.close()

    def test_legacy_receipt_stays_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "evidence.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, plan_hash TEXT, state TEXT, result TEXT)")
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", ("old", "0" * 64, "complete", '{"events":[]}'))
            db.commit()
            db.close()
            store = k.EvidenceStore(path)
            try:
                with self.assertRaises(k.Rejected):
                    store.claim("old", "0" * 64)
                # Migration did not silently rewrite or authenticate old receipts.
                self.assertIsNone(store.db.execute("SELECT result_sha256 FROM runs").fetchone()[0])
                self.assertIsNone(store.claim("new", "2" * 64))
            finally:
                store.close()


class AsyncHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_failure_interrupts_pending_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store = k.EvidenceStore(Path(d) / "evidence.db")
            ready = asyncio.Event()
            calls = 0
            async def transient(args, deps):
                nonlocal calls
                calls += 1
                ready.set()
                raise k.TransientReadError()
            async def denied(args, deps):
                await ready.wait()
                raise PermissionError()
            registry = {"retry": k.ReadOperation("v1", lambda a: None, transient),
                        "denied": k.ReadOperation("v1", lambda a: None, denied)}
            tasks = [{"id": name, "operation": name, "args": {}, "depends_on": []} for name in registry]
            try:
                result = await k.run_reads("attempt", tasks, registry, tuple(registry), store, concurrency=2)
                self.assertEqual(calls, 1)
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["events"][0]["error_type"], "permission_stop")
            finally:
                store.close()

    async def test_receipt_content_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store = k.EvidenceStore(Path(d) / "evidence.db")
            async def read(args, deps):
                return {"data": {}, "source_refs": ["fixture"], "observed_at": time.time(), "warnings": []}
            registry = {"read": k.ReadOperation("v1", lambda a: None, read)}
            tasks = [{"id": "item", "operation": "read", "args": {}, "depends_on": []}]
            try:
                await k.run_reads("run", tasks, registry, ("read",), store)
                raw = store.db.execute("SELECT result FROM runs").fetchone()[0]
                corrupted = json.loads(raw)
                corrupted["status"] = "fabricated"
                store.db.execute("UPDATE runs SET result=?", (json.dumps(corrupted),))
                with self.assertRaises(k.Rejected):
                    await k.run_reads("run", tasks, registry, ("read",), store)
            finally:
                store.close()
