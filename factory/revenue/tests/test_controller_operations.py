import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from factory.revenue.control.cli import main
from factory.revenue.control.contracts import ControlError
from factory.revenue.control.engine import DEFAULT_POLICY
from factory.revenue.control.readiness import inspect_host
from factory.revenue.control.service import MaintainedServer
from factory.revenue.tests.test_controller import ControllerFixture


class MaintenanceTests(ControllerFixture):
    def server(self):
        fixture = self
        class Config:
            def controller(self, path):
                return fixture.open()
        server = MaintainedServer(("127.0.0.1", 0), object, self.path, Config(), clock=lambda: self.now)
        self.addCleanup(server.server_close)
        return server

    def test_expired_lease_fenced_cost_unknown_and_stopped(self):
        job = self.admit()
        claim = self.call("runner", "claim", {"job_id": job["job_id"]})
        self.now += 1000
        server = self.server()
        server.service_actions()
        row = self.c.db.one("SELECT * FROM rc_jobs")
        self.assertEqual(row["state"], "EXPIRED")
        self.assertGreater(row["fence"], claim["fence"])
        self.assertEqual(self.c.db.one("SELECT * FROM rc_reservations")["state"], "UNKNOWN")
        self.assertFalse(self.c.db.one("SELECT * FROM rc_runtime")["enabled"])
        self.assertEqual(self.publisher.sends, 0)

    def test_empty_sweeps_do_not_grow_audit_or_idempotency(self):
        server = self.server()
        before = [len(self.c.db.all("SELECT * FROM " + table)) for table in ("rc_audit", "rc_idempotency")]
        for _ in range(4):
            self.now += 31
            server.service_actions()
        self.assertEqual(before, [len(self.c.db.all("SELECT * FROM " + table)) for table in ("rc_audit", "rc_idempotency")])

    def test_restart_sweeps_queued_job_even_while_stopped(self):
        self.admit()
        self.call("owner_approver", "stop", {"reason": "fixture stop"})
        self.now += 8000
        self.server().service_actions()
        self.assertEqual(self.c.db.one("SELECT state FROM rc_jobs")["state"], "EXPIRED")
        audit = self.c.db.one("SELECT actor,code FROM rc_audit ORDER BY id DESC LIMIT 1")
        self.assertEqual(audit["actor"], "host-expiry-maintenance")
        self.assertEqual(audit["code"], "HOST_EXPIRED_JOBS")

    def test_failure_is_not_swallowed_or_marked_successful(self):
        server = self.server()
        self.now += 31
        previous = server.next_sweep
        with patch.object(server.config, "controller", side_effect=OSError("private detail")):
            with self.assertRaisesRegex(ControlError, "^HOST_MAINTENANCE_FAILED$"):
                server.service_actions()
        self.assertEqual(server.next_sweep, previous)

    def test_sweep_cadence_and_no_live_job_expiration(self):
        self.admit()
        server = self.server()
        server.service_actions()
        with patch.object(server.config, "controller", side_effect=AssertionError("early tick")):
            self.now += 29
            server.service_actions()
        self.assertEqual(self.c.db.one("SELECT state FROM rc_jobs")["state"], "QUEUED")


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "controller.json"
        credentials = self.root / "credentials.json"
        credentials.write_text(json.dumps([{"actor_id": "owner", "role": "owner_approver",
            "token_sha256": hashlib.sha256(b"fixture-only-token").hexdigest(), "expires_at": int(time.time()) + 1000}]))
        credentials.chmod(0o600)
        self.value = {"policy": dict(DEFAULT_POLICY), "credentials_file": str(credentials),
                      "source_adapters": {}, "verifier_profiles": {}, "publisher": None, "payments": None}
        self.write()
        self.addCleanup(patch.stopall)
        patch("factory.revenue.control.readiness.DockerSandbox.capabilities", return_value={"available": False}).start()

    def write(self):
        self.config.write_text(json.dumps(self.value))
        self.config.chmod(0o600)

    def test_valid_but_unconnected_config_fails_and_creates_no_live_db(self):
        report = inspect_host(self.config)
        self.assertTrue(report["configuration_valid"])
        self.assertFalse(report["local_prerequisites_passed"])
        self.assertFalse(report["deployment_verified"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["controller.json", "credentials.json"])
        checks = {r["code"]: r["passed"] for r in report["checks"]}
        self.assertTrue(checks["ACTIVE_CREDENTIAL_OWNER_APPROVER"])
        self.assertFalse(checks["ACTIVE_CREDENTIAL_RUNNER"])
        self.assertFalse(checks["PAYMENT_RECONCILER_CONFIGURED"])

    def test_expired_credentials_not_ready(self):
        path = self.root / "credentials.json"
        rows = json.loads(path.read_text())
        rows[0]["expires_at"] = 1
        path.write_text(json.dumps(rows))
        report = inspect_host(self.config)
        self.assertFalse(next(r["passed"] for r in report["checks"] if r["code"] == "ACTIVE_CREDENTIAL_OWNER_APPROVER"))

    def test_invalid_policy_and_unsafe_config_rejected_without_details(self):
        self.value["policy"]["lease_seconds"] = 9000
        self.write()
        self.assertFalse(inspect_host(self.config)["configuration_valid"])
        self.config.chmod(0o644)
        report = inspect_host(self.config)
        self.assertFalse(report["configuration_valid"])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_cli_exit_78_for_missing_config_is_machine_readable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["doctor", "--config", str(self.root / "absent.json")])
        self.assertEqual(result, 78)
        self.assertFalse(json.loads(output.getvalue())["local_prerequisites_passed"])
