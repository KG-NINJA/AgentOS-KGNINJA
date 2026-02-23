#!/usr/bin/env python3
import http.server
import socketserver
import json
import os
import time
from http.server import SimpleHTTPRequestHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
STATIC_ROOT = os.path.join(ROOT, "factory", "api", "static")
HOST = "127.0.0.1"
PORT = 8787


def _runtime_path(*parts):
    return os.path.join(ROOT, "runtime", *parts)


def _queue_path(*parts):
    return os.path.join(ROOT, "queue", *parts)


def _count_dir(path):
    if not os.path.isdir(path):
        return 0
    try:
        return len(os.listdir(path))
    except Exception:
        return 0


def _is_pid_running(pid_file, pattern):
    if not os.path.isfile(pid_file):
        return False
    try:
        with open(pid_file, "r", encoding="utf-8") as fh:
            pid_text = fh.read().strip()
        pid = int(pid_text)
    except Exception:
        return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    cmd = "ps -p {pid} -o args= 2>/dev/null".format(pid=pid)
    args = os.popen(cmd).read()
    return pattern in args


def _app_server_state():
    pid_file = _runtime_path("app_server.pid")
    if _is_pid_running(pid_file, "codex app-server"):
        return "running"
    return "stopped"


def _api_server_state():
    pid_file = _runtime_path("api.pid")
    if _is_pid_running(pid_file, "factory/api/server.py"):
        return "running"
    return "stopped"


def _active_sessions_count():
    path = _runtime_path("codex_sessions.json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    max_idle = int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400"))
    now = int(time.time())
    count = 0
    for rec in data.values():
        if not isinstance(rec, dict):
            continue
        sid = rec.get("session_id")
        if not sid:
            continue
        last_used = int(rec.get("last_used", 0) or 0)
        if max_idle > 0 and last_used > 0 and now - last_used > max_idle:
            continue
        count += 1
    return count


def _job_state(job_id):
    name = job_id + ".md"
    checks = [
        ("incoming", _queue_path("incoming", name)),
        ("leased", _queue_path("leased", name)),
        ("done", _queue_path("done", name)),
        ("failed", _queue_path("failed", name)),
    ]
    for state, path in checks:
        if os.path.isfile(path):
            return state
    return "unknown"


def _job_meta(state):
    if state == "incoming":
        return "queued", 10, ""
    if state == "leased":
        return "running", 50, ""
    if state == "done":
        return "done", 100, ""
    if state == "failed":
        return "error", 100, "job failed"
    return "unknown", 0, ""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_ROOT, **kwargs)

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = self.headers.get("Content-Length", "0")
        try:
            size = int(length)
        except Exception:
            return None
        if size <= 0:
            return None
        raw = self.rfile.read(size)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        if self.path == "/":
            self.path = "/index.html"
            return super().do_GET()

        if self.path.startswith("/static/"):
            self.path = self.path[len("/static"):]
            return super().do_GET()

        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if self.path == "/status":
            payload = {
                "incoming": _count_dir(_queue_path("incoming")),
                "leased": _count_dir(_queue_path("leased")),
                "done": _count_dir(_queue_path("done")),
                "failed": _count_dir(_queue_path("failed")),
                "app_server": _app_server_state(),
                "sessions": _active_sessions_count(),
                "api_server": _api_server_state(),
            }
            self._send_json(200, payload)
            return

        if self.path.startswith("/job/"):
            job_id = self.path[len("/job/"):].strip()
            if not job_id:
                self._send_json(400, {"error": "missing_job_id"})
                return
            state = _job_state(job_id)
            status, progress, message = _job_meta(state)
            self._send_json(
                200,
                {
                    "job_id": job_id,
                    "state": state,
                    "status": status,
                    "progress": progress,
                    "message": message,
                },
            )
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/build":
            self._send_json(404, {"error": "not_found"})
            return

        payload = self._read_json()
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid_json"})
            return

        idea = payload.get("idea", "")
        if not isinstance(idea, str):
            self._send_json(400, {"error": "idea_must_be_string"})
            return
        if len(idea) == 0:
            self._send_json(400, {"error": "idea_required"})
            return
        if len(idea) > 2000:
            self._send_json(400, {"error": "idea_too_long", "max": 2000})
            return

        os.makedirs(_queue_path("incoming"), exist_ok=True)
        ts = str(int(time.time()))
        job_id = "job_" + ts
        path = _queue_path("incoming", job_id + ".md")
        idx = 1
        while os.path.exists(path):
            job_id = "job_" + ts + "_" + str(idx)
            path = _queue_path("incoming", job_id + ".md")
            idx += 1

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(idea.rstrip() + "\n")

        self._send_json(200, {"job_id": job_id})

    def log_message(self, fmt, *args):
        return


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    os.makedirs(_runtime_path(), exist_ok=True)
    with ThreadingHTTPServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
