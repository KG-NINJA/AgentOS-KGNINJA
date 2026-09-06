"""Required CI integration gate. Missing isolation is a failure, never a skip."""
import os
from pathlib import Path
import subprocess
import tempfile
from .contracts import ControlError
from .sandbox import ArtifactVerifier, DockerSandbox, git_snapshot
from ..sources import digest, json_bytes


def run():
    sandbox = DockerSandbox()
    if not sandbox.capabilities()["available"]:
        raise ControlError("ISOLATION_UNAVAILABLE", 503)
    with tempfile.TemporaryDirectory(prefix="kg-isolation-acceptance-") as temp:
        root = Path(temp)
        secret = root / "host-secret.txt"
        secret.write_text("synthetic-host-canary-do-not-mount")
        secret.chmod(0o600)
        repo = root / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "src/value.py").write_text("value = 1\n")
        test = """import os, pathlib, socket, sys
sys.path.insert(0, '.')
from src.value import value
assert value == 2, 'baseline behavior regressed'
assert 'KG_REVENUE_SECRET_CANARY' not in os.environ
assert not pathlib.Path(HOST_SECRET).exists()
assert not pathlib.Path('/root').exists()
try:
    pathlib.Path('/checks/harness.py').write_text('forged verifier')
except OSError:
    pass
else:
    raise AssertionError('verifier is writable')
try:
    s = socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    s.close()
    raise AssertionError('network escape')
print('isolated behavior, readonly harness, hidden host data and network denial verified')
""".replace("HOST_SECRET", repr(str(secret)))
        (repo / "tests/check.py").write_text(test)
        for command in (["git", "init", "-q"], ["git", "add", "src", "tests"], ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic baseline"]):
            subprocess.run(command, cwd=repo, check=True, capture_output=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        profile = {"repo_url": "https://example.invalid/isolation-fixture", "commit": commit, "repo_path": str(repo),
                   "snapshot_sha256": digest(json_bytes(git_snapshot(repo, commit))), "protected_paths": ["tests"], "checks": [["/usr/bin/python3", "-I", "tests/check.py"]]}
        verifier = ArtifactVerifier({"fixture": profile}, sandbox)
        job = {"repo": {"url": profile["repo_url"], "commit": commit}, "resource_limits": {"max_wall_seconds": 20}}
        original = os.environ.get("KG_REVENUE_SECRET_CANARY")
        os.environ["KG_REVENUE_SECRET_CANARY"] = "synthetic-canary"
        try:
            good = verifier.verify(job, {"files": {"src/value.py": "value = 2\n"}}, "fixture")
            assert good["passed"], good
            bad = verifier.verify(job, {"files": {"src/value.py": "value = 0\n"}}, "fixture")
            assert not bad["passed"], bad
            try:
                verifier.verify(job, {"files": {"tests/check.py": "print('fake pass')\n"}}, "fixture")
            except ControlError as exc:
                assert exc.code == "BASELINE_TEST_MODIFICATION"
            else:
                raise AssertionError("baseline replacement accepted")
        finally:
            if original is None:
                os.environ.pop("KG_REVENUE_SECRET_CANARY", None)
            else:
                os.environ["KG_REVENUE_SECRET_CANARY"] = original
        return {"synthetic": True, "real_isolation": "docker-network-none", "valid_patch": "PASS", "regression_patch": "REJECTED",
                "baseline_replacement": "REJECTED", "host_secret": "INACCESSIBLE", "network": "DENIED", "harness": "READ_ONLY"}


if __name__ == "__main__":
    print(json_bytes(run()).decode())
