"""CLI bridge for existing authorized collectors, Codex runners and reviewers."""
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from .adapters import NoRedirect
from .contracts import ControlError, require, schema, text
from ..sources import digest, json_bytes, strict_json


def invoke(origin, credential_role, operation, payload, key):
    require(re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin), "LOOPBACK_ORIGIN_REQUIRED", 400)
    require(credential_role in ("collector", "agent_operator", "owner_approver", "runner", "verifier", "publisher", "reconciler", "safety_monitor"), "INVALID_CREDENTIAL_ROLE")
    require(re.fullmatch("[a-z]+(?:-[a-z]+)*", operation), "INVALID_OPERATION")
    token = os.environ.get("KG_REVENUE_" + credential_role.upper() + "_TOKEN")
    require(token is not None, "NEEDS_LOGIN", 401)
    text(token, 512)
    raw = json_bytes({"schema_version": "revenue-controller/0.2", "idempotency_key": text(key, 200), "payload": payload})
    require(len(raw) <= 2_000_000, "PAYLOAD_TOO_LARGE", 413)
    request = Request(origin + "/api/" + operation, data=raw, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
            body = response.read(2_000_001)
            require(len(body) <= 2_000_000, "RESPONSE_TOO_LARGE")
            return strict_json(body)
    except HTTPError as exc:
        try:
            code = strict_json(exc.read(10000)).get("error", "CONTROLLER_REJECTED")
        except Exception:
            code = "CONTROLLER_REJECTED"
        raise ControlError(code, exc.code) from exc


def read_input(path):
    p = Path(path).absolute()
    require(not any(x.is_symlink() for x in [p, *p.parents]) and p.stat().st_size <= 2_000_000, "UNSAFE_INPUT_FILE")
    return strict_json(p.read_bytes())


def export_claim(origin, job_id, key, destination):
    p = Path(destination).absolute()
    require(not p.exists() and not any(x.is_symlink() for x in [p, *p.parents]), "EXPORT_PATH_UNSAFE")
    claim = invoke(origin, "runner", "claim", {"job_id": text(job_id, 100)}, key)
    schema("job", claim["job"])
    raw = json_bytes(claim)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
    return {"exported": str(p), "sha256": digest(raw), "job_id": job_id, "lease_until": claim["lease_until"],
            "instructions": "Use the approved isolated runner; heartbeat every 60 seconds, terminate on lease loss. No host-shell fallback."}


def import_result(origin, package, result_path, artifact_path, key):
    claim, result, artifact = read_input(package), read_input(result_path), read_input(artifact_path)
    schema("job", claim["job"])
    schema("execution-result", result)
    require(result["job_id"] == claim["job"]["job_id"] and result["base_commit"] == claim["job"]["repo"]["commit"]
            and result["artifact_sha256"] == digest(json_bytes(artifact)), "RESULT_BINDING_MISMATCH")
    return invoke(origin, "runner", "result", {"fence": claim["fence"], "result": result, "artifact": artifact}, key)
