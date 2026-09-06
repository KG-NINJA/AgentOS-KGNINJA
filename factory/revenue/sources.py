"""Host-owned, bounded public GET adapters. Retrieved text is never authority."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request

MAX_BYTES = 2_000_000
MAX_PAGES = 3
GITHUB = "https://api.github.com/repos/"


class RevenueError(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if not isinstance(value, str):
        raise RevenueError("INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RevenueError("INVALID_TIMESTAMP") from exc
    if parsed.tzinfo is None:
        raise RevenueError("TIMEZONE_REQUIRED")
    return parsed.timestamp()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RevenueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise RevenueError("NONFINITE_JSON")
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        def finite(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise RevenueError("NONFINITE_JSON")
            if isinstance(item, dict):
                for child in item.values():
                    finite(child)
            elif isinstance(item, list):
                for child in item:
                    finite(child)
        finite(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RevenueError("INVALID_JSON") from exc


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Source:
    key: str
    kind: str
    url: str


# Changing destinations is a reviewed host code change, never an agent argument.
SOURCES = {
    s.key: s for s in (
        Source("avu_health", "avu_health", "https://agent-economy.kgninja.dev/health"),
        Source("avu_stats", "avu_stats", "https://agent-economy.kgninja.dev/stats"),
        Source("commerce_integrity", "commerce_integrity", "https://api.kgninja.dev/revenue-log/integrity"),
        Source("buyer_pr", "pull_request", GITHUB + "KG-NINJA/HyperXosist-Agent/pulls/26"),
        Source("runtime_pr", "pull_request", GITHUB + "KG-NINJA/AgentOS-KGNINJA/pulls/7"),
        Source("bounty_pr", "bounty_pr", GITHUB + "VERITOKEN-xx/Veritoken/pulls/726"),
        Source("bounties", "github_issues", GITHUB + "VERITOKEN-xx/Veritoken/issues"),
    )
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RevenueError("REDIRECT_REFUSED")


class PublicReader:
    """No arbitrary URL, POST, cookies, shell, paid calls, or redirect following."""
    def __init__(self, timeout=12, opener=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def get(self, source, page=None):
        if SOURCES.get(source.key) != source:
            raise RevenueError("SOURCE_NOT_ALLOWLISTED")
        url = source.url
        if page is not None:
            if source.kind != "github_issues" or type(page) is not int or not 1 <= page <= MAX_PAGES:
                raise RevenueError("PAGE_NOT_ALLOWED")
            url += f"?state=open&sort=updated&direction=desc&per_page=50&page={page}"
        headers = {"Accept": "application/json", "User-Agent": "AgentOS-Revenue-Observe/0.1",
                   "Cache-Control": "no-cache"}
        if url.startswith(GITHUB):
            headers.update({"Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2026-03-10"})
            token = os.environ.get("KG_REVENUE_GITHUB_READ_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers, method="GET")
        with self.opener.open(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise RevenueError("UNEXPECTED_HTTP_STATUS")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise RevenueError("RESPONSE_TOO_LARGE")
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise RevenueError("EXPECTED_JSON")
            # Captured response headers establish retrieval freshness only, not ledger coverage.
            date = response.headers.get("Date")
            age = response.headers.get("Age", "0")
            try:
                if not str(age).isdigit():
                    raise ValueError()
                observed = parsedate_to_datetime(date).timestamp() if date else None
                if observed is not None:
                    observed = datetime.fromtimestamp(min(observed, instant(stamp()) - int(age)), timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError) as exc:
                raise RevenueError("INVALID_CACHE_TIME") from exc
            return raw, {"response_at": observed, "link": response.headers.get("Link", "")}


def collect_one(source, reader=None):
    reader = reader or PublicReader()
    at = stamp()
    try:
        if source.kind == "github_issues":
            pages, times = [], []
            for page in range(1, MAX_PAGES + 1):
                raw, metadata = reader.get(source, page)
                data = strict_json(raw)
                if not isinstance(data, list):
                    raise RevenueError("INVALID_ISSUE_PAGE")
                pages.append({"raw_json": raw.decode("utf-8"), "sha256": digest(raw)})
                times.append(metadata["response_at"])
                more = bool(re.search(r'rel="next"', metadata["link"]))
                if not more:
                    break
            # Raw bytes survive inside the envelope, with their own hashes.
            raw = json_bytes({"pages": pages, "complete": not more})
            source_at = min(times, key=instant) if all(times) else None
        else:
            raw, metadata = reader.get(source)
            data = strict_json(raw)
            if not isinstance(data, dict):
                raise RevenueError("EXPECTED_OBJECT")
            source_at = data.get("generated_at", data.get("time", metadata["response_at"]))
        if source_at is not None and instant(source_at) > instant(stamp()) + 60:
            raise RevenueError("SOURCE_CLOCK_AHEAD")
        return {"source_key": source.key, "fetched_at": at, "source_at": source_at,
                "ok": True, "raw": raw, "error": None, "retry_after": None}
    except urllib.error.HTTPError as exc:
        code = "NEEDS_LOGIN" if exc.code in (401, 403) else "SOURCE_HTTP_ERROR"
        if exc.code == 429 or (exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0"):
            code = "SOURCE_RATE_LIMITED"
        retry = exc.headers.get("Retry-After")
        seconds = int(retry) if retry and retry.isdigit() else 300
        if code == "NEEDS_LOGIN":
            seconds = 3600
        return failure(source.key, at, code, max(300, min(seconds, 86400)))
    except RevenueError as exc:
        return failure(source.key, at, str(exc))
    except (OSError, TimeoutError):
        return failure(source.key, at, "SOURCE_UNAVAILABLE")


def failure(key, at, code, delay=300):
    return {"source_key": key, "fetched_at": at, "source_at": None, "ok": False,
            "raw": json_bytes({"error": code}), "error": code,
            "retry_after": instant(at) + delay}
