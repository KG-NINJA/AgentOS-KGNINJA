"""Local deployment checks. No production DB access, writes or live API probes."""
from pathlib import Path
import shutil
import tempfile
import time
from .contracts import ControlError
from .sandbox import DockerSandbox
from .service import HostConfiguration
from ..sources import RevenueError


def inspect_host(config_path):
    report = {
        "schema_version": "revenue-readiness/0.1",
        "checked_at": int(time.time()),
        "configuration_valid": False,
        "configuration_sha256": None,
        "checks": [],
        "deployment_verified": False,
        "runner_login_and_billing_verified": False,
        "source_connectivity_verified": False,
        "payment_receipts_verified": False,
        "payment_execution": False,
    }
    def check(name, ok):
        report["checks"].append({"code": name, "passed": bool(ok)})
    try:
        host = HostConfiguration(config_path)
        # Validate policy using a disposable controller, never the live ledger.
        with tempfile.TemporaryDirectory(prefix="kg-revenue-readiness-") as directory:
            c = host.controller(Path(directory) / "check.sqlite3")
            try:
                report["configuration_sha256"] = c.fingerprint
            finally:
                c.close()
        report["configuration_valid"] = True
        now = time.time()
        live_roles = {c["role"] for c in host.auth.credentials if c["expires_at"] > now}
        for role in ("owner_approver", "collector", "agent_operator", "runner", "verifier", "reconciler"):
            check("ACTIVE_CREDENTIAL_" + role.upper(), role in live_roles)
        check("SOURCES_CONFIGURED", bool(host.sources))
        check("SOURCE_ALLOWLIST_MATCHES_ADAPTERS", set(host.policy["sources"]) == set(host.sources))
        check("REPOSITORIES_CONFIGURED", bool(host.policy["repositories"]))
        check("VERIFIER_CONFIGURED", host.verifier is not None)
        check("ISOLATION_BACKEND_AVAILABLE", DockerSandbox().capabilities()["available"])
        check("CODEX_BINARY_AVAILABLE", shutil.which("codex") is not None)
        check("PAYMENT_RECONCILER_CONFIGURED", host.payments is not None and bool(host.payments.networks))
        if host.policy["allow_publication"]:
            check("ACTIVE_CREDENTIAL_PUBLISHER", "publisher" in live_roles)
            check("PUBLISHER_CONFIGURED", host.publisher is not None)
            check("PUBLICATION_TARGETS_MATCH", host.publisher is not None and
                  bool(host.policy["publication_targets"]) and
                  set(host.policy["publication_targets"]) == set(host.publisher.targets))
        report["publication_enabled_by_policy"] = host.policy["allow_publication"]
    except (ControlError, RevenueError, OSError, ValueError, TypeError, KeyError, AttributeError):
        # Do not return exception text: paths/configuration can contain secrets.
        check("HOST_CONFIGURATION_VALID", False)
    report["local_prerequisites_passed"] = report["configuration_valid"] and all(c["passed"] for c in report["checks"])
    report["remaining_live_checks"] = ["ISOLATION_ACCEPTANCE_ON_TARGET_HOST", "AUTHENTICATED_BUDGETED_RUNNER",
                                       "SOURCE_REFRESH", "SERVICE_INSTALLATION", "EXTERNAL_DELIVERY_AND_RECEIPT"]
    return report
