"""Network-isolated verification of host-pinned source and immutable tests.

There is deliberately no fallback to an ordinary subprocess for project code.
Docker is the CI/host backend; bwrap is an optional host capability probe only.
"""
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from .contracts import fields, relative, require, text
from ..sources import digest, json_bytes, strict_json

HARNESS = r'''import json, os, pathlib, subprocess, sys
request = json.loads(pathlib.Path('/input/request.json').read_text())
root = pathlib.Path('/work/project')
root.mkdir()
for name, content in request['files'].items():
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
results = []
for argv in request['checks']:
    try:
        log = pathlib.Path('/work/log.txt')
        with log.open('wb') as out:
            # The trusted harness owns the immutable project and log. Project
            # code runs as another UID and cannot replace tests or signal the
            # harness. No credentials are present in either process.
            run = subprocess.run(argv, cwd=root, user=65534, group=65534, extra_groups=(), env={'PATH':'/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}, stdout=out, stderr=subprocess.STDOUT, timeout=request['check_timeout'])
        results.append({'argv':argv,'exit_code':run.returncode,'log':log.read_bytes()[:64000].decode('utf-8','replace')})
    except Exception as e:
        results.append({'argv':argv,'exit_code':None,'error':type(e).__name__})
print(json.dumps({'checks':results}))
sys.exit(0)
'''


def source_path(value):
    p = PurePosixPath(value)
    require(isinstance(value, str) and not p.is_absolute() and str(p) == value and ".." not in p.parts
            and "\\" not in value and ":" not in value and not any(x in (".git", ".env", ".dev.vars", "credentials.json") or x.startswith(".env.") for x in p.parts), "UNSAFE_SOURCE_PATH")
    return value


def git_snapshot(repo_path, commit):
    """Read committed tracked files without checkout, hooks, networking or filters."""
    import re
    require(re.fullmatch("[0-9a-f]{40,64}", commit), "INVALID_COMMIT")
    path = Path(repo_path).resolve(strict=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
    run = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(path), "archive", "--format=tar", commit],
                         env=env, capture_output=True, timeout=30, check=False)
    require(run.returncode == 0 and len(run.stdout) <= 20_000_000, "PINNED_SOURCE_UNAVAILABLE")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(run.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            require(member.isfile() and member.size <= 1_000_000 and len(files) < 3000, "SOURCE_ARCHIVE_UNSAFE")
            name = source_path(member.name)
            require(name not in files, "DUPLICATE_SOURCE_PATH")
            try:
                files[name] = archive.extractfile(member).read().decode("utf-8")
            except UnicodeDecodeError:
                # Initial verifier profiles are text-source projects only. Assets
                # must be excluded by a reviewed source profile, never silently lost.
                require(False, "BINARY_SOURCE_PROFILE_REQUIRED")
    return files


class DockerSandbox:
    def __init__(self, binary=None):
        self.binary = binary or shutil.which("docker")
        self.image = None

    def capabilities(self):
        if not self.binary:
            return {"available": False, "code": "ISOLATION_UNAVAILABLE"}
        try:
            run = subprocess.run([self.binary, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=10, env=self._env())
            return {"available": run.returncode == 0, "backend": "docker", "network": "none"}
        except (OSError, subprocess.TimeoutExpired):
            return {"available": False, "code": "ISOLATION_UNAVAILABLE"}

    @staticmethod
    def _env():
        # Never propagate an arbitrary DOCKER_HOST, SSH socket, token or host env.
        return {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}

    def _image(self):
        require(self.capabilities()["available"], "ISOLATION_UNAVAILABLE", 503)
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w"):
            pass
        run = subprocess.run([self.binary, "import", "-"], input=data.getvalue(), capture_output=True, timeout=15, env=self._env())
        image = run.stdout.decode().strip()
        require(run.returncode == 0 and image.startswith("sha256:") and len(image) == 71, "LOCAL_SANDBOX_IMAGE_FAILED", 503)
        return image

    def command(self, image, name, inputs, checks, timeout):
        argv = [self.binary, "run", "--pull=never", "--rm", "--name", name, "--network=none", "--read-only",
                "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--cpus=1",
                "--user=0:0", "--ulimit", "fsize=1048576:1048576", "--ulimit", "nofile=128:128",
                "--tmpfs", "/work:rw,noexec,nosuid,nodev,size=128m,mode=1777", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777"]
        for host, target in [("/usr", "/usr"), ("/lib", "/lib"), ("/lib64", "/lib64"), ("/bin", "/bin"), (str(inputs), "/input"), (str(checks), "/checks")]:
            if Path(host).exists():
                argv += ["--mount", f"type=bind,source={Path(host).resolve()},target={target},readonly"]
        argv += [image, "/usr/bin/python3", "-I", "/checks/harness.py"]
        return argv

    def run(self, files, commands, timeout):
        import uuid
        require(type(timeout) is int and 1 <= timeout <= 7200, "INVALID_TEST_TIMEOUT")
        require(isinstance(files, dict) and len(json_bytes(files)) <= 20_000_000, "SOURCE_TOO_LARGE")
        for name, content in files.items():
            source_path(name)
            require(isinstance(content, str), "NON_TEXT_SOURCE")
        require(isinstance(commands, list) and 1 <= len(commands) <= 10, "REQUIRED_TESTS_MISSING")
        for command in commands:
            require(isinstance(command, list) and 1 <= len(command) <= 30 and all(isinstance(x, str) and "\x00" not in x for x in command), "INVALID_CHECK_COMMAND")
        image = self._image()
        container = "kg-revenue-" + uuid.uuid4().hex
        try:
            with tempfile.TemporaryDirectory(prefix="kg-verifier-") as directory:
                root = Path(directory)
                root.chmod(0o755)
                inputs, checks = root / "input", root / "checks"
                inputs.mkdir(mode=0o755)
                checks.mkdir(mode=0o755)
                (inputs / "request.json").write_bytes(json_bytes({"files": files, "checks": commands, "check_timeout": max(1, timeout // len(commands))}))
                (checks / "harness.py").write_text(HARNESS)
                for p in (inputs / "request.json", checks / "harness.py"):
                    p.chmod(0o444)
                argv = self.command(image, container, inputs, checks, timeout)
                try:
                    run = subprocess.run(argv, capture_output=True, timeout=timeout + 10, env=self._env())
                    require(run.returncode == 0 and len(run.stdout) <= 1_000_000, "ISOLATED_CHECK_FAILED")
                    result = strict_json(run.stdout)
                    require(isinstance(result, dict) and isinstance(result.get("checks"), list) and len(result["checks"]) == len(commands), "ISOLATED_REPORT_INVALID")
                    return result["checks"]
                finally:
                    subprocess.run([self.binary, "rm", "-f", container], capture_output=True, timeout=10, env=self._env())
        finally:
            subprocess.run([self.binary, "image", "rm", image], capture_output=True, timeout=10, env=self._env())


class ArtifactVerifier:
    def __init__(self, profiles, sandbox=None):
        self.profiles, self.sandbox = profiles, sandbox or DockerSandbox()
        self.fingerprint = digest(json_bytes({"profiles": profiles, "code": digest(Path(__file__).read_bytes()), "backend": "docker-readonly-network-none-uid-separated-v2"}))

    def verify(self, job, artifact, profile_key):
        profile = self.profiles.get(profile_key)
        require(profile is not None, "CHECK_PROFILE_UNAVAILABLE")
        fields(profile, ("repo_url", "commit", "repo_path", "snapshot_sha256", "protected_paths", "checks"))
        require(profile["repo_url"] == job["repo"]["url"] and profile["commit"] == job["repo"]["commit"], "BASE_COMMIT_MISMATCH")
        files = git_snapshot(profile["repo_path"], profile["commit"])
        require(digest(json_bytes(files)) == profile["snapshot_sha256"], "BASE_SNAPSHOT_CHANGED")
        require(profile["protected_paths"] and profile["checks"], "BASELINE_TESTS_REQUIRED")
        protected = {path: content for path, content in files.items() if any(path == p or path.startswith(p + "/") for p in profile["protected_paths"])}
        require(protected, "BASELINE_TESTS_MISSING")
        for path, content in artifact["files"].items():
            relative(path)
            require(not any(path == p or path.startswith(p + "/") for p in profile["protected_paths"]), "BASELINE_TEST_MODIFICATION")
            require(not any(existing.startswith(path + "/") or path.startswith(existing + "/") for existing in files if existing != path), "FILE_DIRECTORY_COLLISION")
            files[path] = content
        checks = self.sandbox.run(files, profile["checks"], job["resource_limits"]["max_wall_seconds"])
        passed = all(c.get("exit_code") == 0 for c in checks)
        return {"passed": passed, "checks": checks, "evidence_sha256": digest(json_bytes(checks)), "isolation": "docker-network-none"}
