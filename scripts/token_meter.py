#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PLUGIN_ID = "kimi-code-token-meter"
VERSION = "2.1.0"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
DEFAULT_IDLE_TIMEOUT = 15 * 60
RECENT_REQUEST_LIMIT = 100
REQUEST_HISTORY_RETENTION = 500


def kimi_home():
    value = os.environ.get("KIMI_CODE_HOME", "~/.kimi-code")
    return Path(value).expanduser().resolve()


def runtime_dir(home):
    return home / PLUGIN_ID


def read_json(path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return None


def atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("{}.{}.tmp".format(path.name, os.getpid()))
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def valid_session_id(value):
    return isinstance(value, str) and SESSION_ID_PATTERN.fullmatch(value) is not None


def track_session(home):
    try:
        payload = json.loads(sys.stdin.read())
        session_id = payload.get("session_id")
        if not valid_session_id(session_id):
            return 0
        atomic_write_json(
            runtime_dir(home) / "active-session.json",
            {"session_id": session_id, "updated_at": int(time.time() * 1000)},
        )
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return 0


def wire_files(session_dir):
    try:
        return set(session_dir.glob("agents/*/wire.jsonl"))
    except OSError:
        return set()


def session_activity(session_dir):
    latest = 0.0
    for path in wire_files(session_dir):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    if latest:
        return latest
    try:
        return session_dir.stat().st_mtime
    except OSError:
        return 0.0


def newest_session(home):
    sessions_root = home / "sessions"
    candidates = []
    try:
        for candidate in sessions_root.glob("*/*"):
            if candidate.is_dir() and wire_files(candidate):
                candidates.append(candidate)
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=session_activity)


def session_by_id(home, session_id):
    if not valid_session_id(session_id):
        return None
    try:
        matches = [
            path
            for path in (home / "sessions").glob("*/" + session_id)
            if path.is_dir()
        ]
    except OSError:
        return None
    if not matches:
        return None
    return max(matches, key=session_activity)


def active_session(home):
    tracked = read_json(runtime_dir(home) / "active-session.json")
    session_id = tracked.get("session_id") if isinstance(tracked, dict) else None
    updated_at = number(tracked.get("updated_at")) if isinstance(tracked, dict) else 0.0
    age_ms = time.time() * 1000.0 - updated_at
    if 0 <= age_ms < 30000:
        selected = session_by_id(home, session_id)
        if selected is not None:
            return selected
    return newest_session(home)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if value < 0:
        return 0.0
    return float(value)


class LogTruncated(Exception):
    pass


class WireTail:
    def __init__(self, path):
        self.path = path
        self.agent_id = path.parent.name
        self.offset = 0
        self.buffer = b""
        self.pending_request_ms = None

    def read_into(self, metrics):
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                raise LogTruncated(str(self.path))
            if size == self.offset:
                self._process_buffered(metrics)
                return
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
        except FileNotFoundError:
            raise LogTruncated(str(self.path))
        self.buffer += data
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            self._process_line(line, metrics)
        self._process_buffered(metrics)

    def _process_buffered(self, metrics):
        if not self.buffer:
            return
        if b"llm.request" not in self.buffer and b"usage.record" not in self.buffer:
            return
        if self._process_line(self.buffer, metrics):
            self.buffer = b""

    def _process_line(self, line, metrics):
        if b"llm.request" not in line and b"usage.record" not in line:
            return True
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(event, dict):
            return True
        event_type = event.get("type")
        event_time = number(event.get("time"))
        if event_type == "llm.request":
            if event_time:
                self.pending_request_ms = event_time
                metrics.last_request_ms = max(metrics.last_request_ms, event_time)
        elif event_type == "usage.record":
            if event.get("usageScope", "turn") != "turn":
                return True
            usage = event.get("usage")
            if not isinstance(usage, dict) or not event_time:
                return True
            metrics.add_usage(
                usage=usage,
                event_time=event_time,
                request_time=self.pending_request_ms,
                model=event.get("model"),
                agent_id=self.agent_id,
            )
            self.pending_request_ms = None
        return True


class SessionMetrics:
    def __init__(self, home, session_id=None, retain_history=True, history_limit=REQUEST_HISTORY_RETENTION):
        self.home = home
        self.session_id = session_id
        self.retain_history = retain_history
        self.history_limit = history_limit
        self.session_dir = None
        self.tails = {}
        self._clear_totals()

    def _clear_totals(self):
        self.cache_read = 0.0
        self.cache_creation = 0.0
        self.input_other = 0.0
        self.output = 0.0
        self.request_count = 0
        self.last_request_ms = 0.0
        self.last_usage_ms = 0.0
        self.latest_speed = None
        self.request_history = []

    def _select_session(self, session_dir):
        if session_dir == self.session_dir:
            return
        self.session_dir = session_dir
        self.tails = {}
        self._clear_totals()

    def add_usage(self, usage, event_time, request_time, model, agent_id):
        cache_read = number(usage.get("inputCacheRead"))
        cache_creation = number(usage.get("inputCacheCreation"))
        input_other = number(usage.get("inputOther"))
        output = number(usage.get("output"))
        self.cache_read += cache_read
        self.cache_creation += cache_creation
        self.input_other += input_other
        self.output += output
        self.request_count += 1
        self.last_usage_ms = max(self.last_usage_ms, event_time)

        duration_ms = None
        speed = None
        if request_time is not None and event_time > request_time:
            duration_ms = event_time - request_time
            speed = output * 1000.0 / duration_ms

        if self.retain_history:
            self.request_history.append({
                "time": int(event_time),
                "agentId": agent_id if isinstance(agent_id, str) else None,
                "model": model if isinstance(model, str) else None,
                "uncachedInputTokens": int(input_other + cache_creation),
                "cacheReadTokens": int(cache_read),
                "cacheCreationTokens": int(cache_creation),
                "inputOtherTokens": int(input_other),
                "inputTokens": int(cache_read + cache_creation + input_other),
                "outputTokens": int(output),
                "speedTokensPerSecond": round(speed, 3) if speed is not None else None,
                "durationMs": int(duration_ms) if duration_ms is not None else None,
            })
            if self.history_limit is not None and len(self.request_history) > self.history_limit * 2:
                self.request_history.sort(key=lambda item: item["time"])
                self.request_history = self.request_history[-self.history_limit:]

        if speed is not None and (
            self.latest_speed is None
            or event_time >= self.latest_speed["sample_time"]
        ):
            self.latest_speed = {
                "value": speed,
                "sample_time": event_time,
                "duration_ms": duration_ms,
                "model": model if isinstance(model, str) else None,
            }

    def poll(self):
        if self.session_id:
            selected = session_by_id(self.home, self.session_id)
        else:
            selected = active_session(self.home)
        self._select_session(selected)
        if selected is None:
            return self.snapshot()
        current_paths = wire_files(selected)
        if any(path not in current_paths for path in self.tails):
            self.tails = {}
            self._clear_totals()
        for path in sorted(current_paths):
            if path not in self.tails:
                self.tails[path] = WireTail(path)
        try:
            for path in list(self.tails):
                self.tails[path].read_into(self)
        except (LogTruncated, OSError):
            self.tails = {}
            self._clear_totals()
            for path in sorted(wire_files(selected)):
                self.tails[path] = WireTail(path)
            try:
                for tail in self.tails.values():
                    tail.read_into(self)
            except (LogTruncated, OSError):
                self.tails = {}
                self._clear_totals()
        return self.snapshot()

    def snapshot(self):
        total_input = self.cache_read + self.cache_creation + self.input_other
        now_ms = time.time() * 1000.0
        generating = any(
            tail.pending_request_ms is not None
            and 0 <= now_ms - tail.pending_request_ms < 120000
            for tail in self.tails.values()
        )
        speed = None
        speed_sample_time = None
        speed_duration_ms = None
        speed_model = None
        if self.latest_speed:
            speed = round(self.latest_speed["value"], 3)
            speed_sample_time = int(self.latest_speed["sample_time"])
            speed_duration_ms = int(self.latest_speed["duration_ms"])
            speed_model = self.latest_speed["model"]
        recent_requests = sorted(
            self.request_history,
            key=lambda item: item["time"],
            reverse=True,
        )[:RECENT_REQUEST_LIMIT]
        return {
            "service": PLUGIN_ID,
            "sessionId": self.session_dir.name if self.session_dir else None,
            "sessionFound": self.session_dir is not None,
            "speedTokensPerSecond": speed,
            "speedSampleTime": speed_sample_time,
            "speedDurationMs": speed_duration_ms,
            "speedModel": speed_model,
            "cacheHitPercent": round(self.cache_read * 100.0 / total_input, 3) if total_input else None,
            "cacheReadTokens": int(self.cache_read),
            "cacheCreationTokens": int(self.cache_creation),
            "inputOtherTokens": int(self.input_other),
            "inputTokens": int(total_input),
            "outputTokens": int(self.output),
            "requestCount": self.request_count,
            "generating": generating,
            "lastRequestTime": int(self.last_request_ms) if self.last_request_ms else None,
            "lastUsageTime": int(self.last_usage_ms) if self.last_usage_ms else None,
            "serverTime": int(now_ms),
            "recentRequests": recent_requests,
        }


def session_directories(home):
    try:
        return {
            candidate
            for candidate in (home / "sessions").glob("*/*")
            if candidate.is_dir() and wire_files(candidate)
        }
    except OSError:
        return set()


def session_metadata(session_dir):
    state = read_json(session_dir / "state.json")
    if not isinstance(state, dict):
        return session_dir.name, None
    title = state.get("title")
    work_dir = state.get("cwd")
    return (
        title if isinstance(title, str) and title else session_dir.name,
        work_dir if isinstance(work_dir, str) else None,
    )


def session_is_archived(session_dir):
    state = read_json(session_dir / "state.json")
    return isinstance(state, dict) and state.get("archived") is True


class GlobalMetrics:
    def __init__(self, home, cache_seconds=3.0):
        self.home = home
        self.cache_seconds = cache_seconds
        self.engines = {}
        self.cached_at = 0.0
        self.cached_snapshot = None

    def poll(self):
        now = time.monotonic()
        if self.cached_snapshot is not None and now - self.cached_at < self.cache_seconds:
            return self.cached_snapshot
        directories = {
            directory
            for directory in session_directories(self.home)
            if not session_is_archived(directory)
        }
        for removed in set(self.engines) - directories:
            del self.engines[removed]
        for directory in sorted(directories):
            if directory not in self.engines:
                self.engines[directory] = SessionMetrics(
                    self.home,
                    directory.name,
                    retain_history=True,
                    history_limit=None,
                )
        records = []
        for directory, engine in self.engines.items():
            snapshot = engine.poll()
            if snapshot["requestCount"] == 0:
                continue
            title, work_dir = session_metadata(directory)
            records.append({
                "sessionId": directory.name,
                "title": title,
                "workDir": work_dir,
                "requestCount": snapshot["requestCount"],
                "inputTokens": snapshot["inputTokens"],
                "outputTokens": snapshot["outputTokens"],
                "cacheReadTokens": snapshot["cacheReadTokens"],
                "cacheHitPercent": snapshot["cacheHitPercent"],
                "speedTokensPerSecond": snapshot["speedTokensPerSecond"],
                "lastUsageTime": snapshot["lastUsageTime"],
            })
        records.sort(key=lambda item: item["lastUsageTime"] or 0, reverse=True)
        request_count = sum(item["requestCount"] for item in records)
        input_tokens = sum(item["inputTokens"] for item in records)
        output_tokens = sum(item["outputTokens"] for item in records)
        cache_read = sum(item["cacheReadTokens"] for item in records)
        self.cached_snapshot = {
            "sessionCount": len(records),
            "requestCount": request_count,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": cache_read,
            "cacheHitPercent": round(cache_read * 100.0 / input_tokens, 3) if input_tokens else None,
            "sessions": records,
        }
        self.cached_at = now
        return self.cached_snapshot

    @staticmethod
    def project_name(work_dir):
        if not work_dir:
            return "未记录项目"
        normalized = work_dir.replace("\\", "/").rstrip("/")
        return normalized.rsplit("/", 1)[-1] or normalized

    @staticmethod
    def aggregate(records):
        input_tokens = sum(item["inputTokens"] for item in records)
        output_tokens = sum(item["outputTokens"] for item in records)
        cache_read = sum(item["cacheReadTokens"] for item in records)
        timed = [item for item in records if item.get("durationMs")]
        duration_ms = sum(item["durationMs"] for item in timed)
        timed_output = sum(item["outputTokens"] for item in timed)
        latest = max(records, key=lambda item: item["time"], default=None)
        latest_timed = max(timed, key=lambda item: item["time"], default=None)
        return {
            "requestCount": len(records),
            "inputTokens": input_tokens,
            "uncachedInputTokens": sum(item["uncachedInputTokens"] for item in records),
            "cacheReadTokens": cache_read,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
            "cacheHitPercent": round(cache_read * 100.0 / input_tokens, 3) if input_tokens else None,
            "speedTokensPerSecond": round(timed_output * 1000.0 / duration_ms, 3) if duration_ms else None,
            "latestSpeedTokensPerSecond": latest_timed.get("speedTokensPerSecond") if latest_timed else None,
            "lastUsageTime": latest["time"] if latest else None,
        }

    def grouped_rows(self, records, key_function):
        groups = {}
        for record in records:
            groups.setdefault(key_function(record), []).append(record)
        rows = []
        for key, items in groups.items():
            row = self.aggregate(items)
            row["key"] = key
            rows.append(row)
        rows.sort(key=lambda item: item["totalTokens"], reverse=True)
        return rows

    def dashboard(self, start_ms=None, end_ms=None):
        self.poll()
        records = []
        for directory, engine in self.engines.items():
            title, work_dir = session_metadata(directory)
            for record in engine.request_history:
                if start_ms is not None and record["time"] < start_ms:
                    continue
                if end_ms is not None and record["time"] >= end_ms:
                    continue
                item = dict(record)
                item.update({
                    "sessionId": directory.name,
                    "title": title,
                    "workDir": work_dir,
                    "project": self.project_name(work_dir),
                })
                records.append(item)

        summary = self.aggregate(records)
        summary["sessionCount"] = len({item["sessionId"] for item in records})
        summary["modelCount"] = len({item["model"] or "未知模型" for item in records})

        daily_groups = {}
        for record in records:
            day = datetime.fromtimestamp(record["time"] / 1000.0).date().isoformat()
            daily_groups.setdefault(day, []).append(record)
        daily = []
        for day, items in daily_groups.items():
            row = self.aggregate(items)
            row["date"] = day
            daily.append(row)
        daily.sort(key=lambda item: item["date"])

        models = self.grouped_rows(records, lambda item: item["model"] or "未知模型")
        for row in models:
            row["name"] = row.pop("key")

        sessions = self.grouped_rows(records, lambda item: item["sessionId"])
        metadata = {item["sessionId"]: item for item in records}
        for row in sessions:
            session_id = row.pop("key")
            item = metadata[session_id]
            row.update({
                "sessionId": session_id,
                "name": item["title"],
                "workDir": item["workDir"],
                "project": item["project"],
            })

        projects = self.grouped_rows(records, lambda item: item["workDir"] or "")
        for row in projects:
            work_dir = row.pop("key")
            items = [item for item in records if (item["workDir"] or "") == work_dir]
            row.update({
                "name": self.project_name(work_dir),
                "workDir": work_dir or None,
                "sessionCount": len({item["sessionId"] for item in items}),
            })

        return {
            "summary": summary,
            "daily": daily,
            "models": models,
            "sessions": sessions,
            "projects": projects,
            "serverTime": int(time.time() * 1000.0),
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, home, page, idle_timeout):
        super().__init__(address, handler)
        self.home = home
        self.engines = {}
        self.global_metrics = GlobalMetrics(home)
        self.metrics_lock = threading.Lock()
        self.page = page
        self.idle_timeout = idle_timeout
        self.last_dashboard_poll = time.monotonic()

    def metrics_snapshot(self, session_id):
        key = session_id or ""
        with self.metrics_lock:
            if key not in self.engines:
                self.engines[key] = SessionMetrics(self.home, session_id)
            snapshot = self.engines[key].poll()
            snapshot["global"] = self.global_metrics.poll()
            return snapshot

    def dashboard_snapshot(self, start_ms, end_ms):
        with self.metrics_lock:
            return self.global_metrics.dashboard(start_ms, end_ms)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "KimiCodeTokenMeter/{}".format(VERSION)
    sys_version = ""

    def log_message(self, format_string, *args):
        return

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    @staticmethod
    def date_ms(value, end=False):
        if value is None:
            return None
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if end:
            parsed += timedelta(days=1)
        return int(parsed.timestamp() * 1000.0)

    def do_GET(self):
        expected_host = "127.0.0.1:{}".format(self.server.server_address[1])
        if self.headers.get("Host") != expected_host:
            self.send_json({"error": "forbidden"}, status=403)
            return
        request = urlsplit(self.path)
        if request.path == "/api/health":
            self.send_json({"ok": True, "service": PLUGIN_ID, "version": VERSION})
            return
        if request.path == "/api/metrics":
            self.server.last_dashboard_poll = time.monotonic()
            session_id = parse_qs(request.query).get("session", [None])[0]
            if session_id is not None and not valid_session_id(session_id):
                self.send_json({"error": "invalid session id"}, status=400)
                return
            self.send_json(self.server.metrics_snapshot(session_id))
            return
        if request.path == "/api/dashboard":
            self.server.last_dashboard_poll = time.monotonic()
            query = parse_qs(request.query)
            try:
                start_ms = self.date_ms(query.get("start", [None])[0])
                end_ms = self.date_ms(query.get("end", [None])[0], end=True)
                if start_ms is not None and end_ms is not None and start_ms >= end_ms:
                    raise ValueError("开始日期不能晚于结束日期")
            except ValueError as error:
                self.send_json({"error": str(error)}, status=400)
                return
            self.send_json(self.server.dashboard_snapshot(start_ms, end_ms))
            return
        if request.path == "/":
            self.server.last_dashboard_poll = time.monotonic()
            self.send_bytes(200, self.server.page, "text/html; charset=utf-8")
            return
        if request.path == "/favicon.ico":
            self.send_bytes(204, b"", "image/x-icon")
            return
        self.send_json({"error": "not found"}, status=404)


def server_state_path(home):
    return runtime_dir(home) / "server.json"


def healthy_url(home):
    state = read_json(server_state_path(home))
    if not isinstance(state, dict):
        return None
    url = state.get("url")
    pid = state.get("pid")
    if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        with urllib.request.urlopen(url + "api/health", timeout=0.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if (
            payload.get("ok") is True
            and payload.get("service") == PLUGIN_ID
            and payload.get("version") == VERSION
        ):
            return url
    except (OSError, ValueError, AttributeError):
        pass
    return None


def remove_server_state(home, pid):
    path = server_state_path(home)
    state = read_json(path)
    if isinstance(state, dict) and state.get("pid") == pid:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def serve(home, idle_timeout):
    page_path = Path(__file__).resolve().parent.parent / "web" / "index.html"
    page = page_path.read_bytes()
    runtime_dir(home).mkdir(parents=True, exist_ok=True)
    server = DashboardHTTPServer(("127.0.0.1", 0), DashboardHandler, home, page, idle_timeout)
    url = "http://127.0.0.1:{}/".format(server.server_address[1])
    atomic_write_json(
        server_state_path(home),
        {"pid": os.getpid(), "url": url, "started_at": int(time.time() * 1000)},
    )

    def stop_when_idle():
        while True:
            time.sleep(min(5.0, max(0.1, idle_timeout / 4.0)))
            if time.monotonic() - server.last_dashboard_poll >= idle_timeout:
                server.shutdown()
                return

    watchdog = threading.Thread(target=stop_when_idle, name="idle-watchdog", daemon=True)
    watchdog.start()
    print(url, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        remove_server_state(home, os.getpid())
    return 0


def dashboard_url(home, base_url):
    session = newest_session(home)
    if session is not None and valid_session_id(session.name):
        return base_url + "?session=" + session.name
    return base_url


def launch(home, idle_timeout):
    existing = healthy_url(home)
    if existing:
        print(dashboard_url(home, existing))
        return 0
    runtime_dir(home).mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir(home) / "server.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--serve",
        "--idle-timeout",
        str(idle_timeout),
    ]
    with log_path.open("ab") as log:
        options = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "cwd": str(Path(__file__).resolve().parent.parent),
            "close_fds": True,
        }
        if os.name == "nt":
            options["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        state = read_json(server_state_path(home))
        if isinstance(state, dict) and state.get("pid") == process.pid:
            url = healthy_url(home)
            if url:
                print(dashboard_url(home, url))
                return 0
        if process.poll() is not None:
            break
        time.sleep(0.08)
    if process.poll() is None:
        process.terminate()
    raise RuntimeError("看板服务启动失败，请检查日志：{}".format(log_path))


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Kimi Code App token speed and cache hit dashboard")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--track-session", action="store_true")
    actions.add_argument("--metrics", action="store_true")
    actions.add_argument("--launch", action="store_true")
    actions.add_argument("--serve", action="store_true")
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT)
    args = parser.parse_args(argv)
    if args.idle_timeout <= 0:
        parser.error("--idle-timeout must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    home = kimi_home()
    if args.track_session:
        return track_session(home)
    if args.metrics:
        print(json.dumps(SessionMetrics(home).poll(), ensure_ascii=False, indent=2))
        return 0
    if args.launch:
        return launch(home, args.idle_timeout)
    return serve(home, args.idle_timeout)


if __name__ == "__main__":
    sys.exit(main())
