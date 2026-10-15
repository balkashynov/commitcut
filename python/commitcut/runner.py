"""Own processes, inject one fail-stop crash, restart and check persisted state.

The proxy never kills processes. It freezes at the cut; this supervisor kills
only its own application process group and waits before tearing down sockets.
"""
from __future__ import annotations

import hashlib
import fcntl
import contextlib
import tempfile
import hmac
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class InvalidRun(RuntimeError):
    """No trustworthy answer; never turn this into a passing crash test."""


@dataclass
class Config:
    path: Path
    data: dict
    database: str
    proxy: str
    psql: str
    include_sql: bool = False

    @classmethod
    def load(cls, path, include_sql=False):
        path = Path(path).resolve()
        with path.open("rb") as f:
            data = tomllib.load(f)
        database = os.environ.get(data.get("database_env", "COMMITCUT_DATABASE_URL"), "")
        parsed = urllib.parse.urlsplit(database)
        if parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise InvalidRun("Set COMMITCUT_DATABASE_URL to a dedicated loopback PostgreSQL test database")
        if not parsed.path.strip("/"):
            raise InvalidRun("A named test database is required")
        if not data.get("reset_sql") or not data.get("assertions"):
            raise InvalidRun("Config needs reset_sql and at least one assertion")
        command = data.get("app", {}).get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise InvalidRun("app.command must be a nonempty argument array")
        if not isinstance(data.get("name"), str) or not data["name"]:
            raise InvalidRun("Config needs a nonempty name")
        timeout = data.get("timeout_seconds", 10)
        retries = data.get("redeliveries", 2)
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise InvalidRun("timeout_seconds must be between 0 and 300")
        if not isinstance(retries, int) or isinstance(retries, bool) or not 1 <= retries <= 100:
            raise InvalidRun("redeliveries must be an integer between 1 and 100")
        request = data.get("request", {})
        if not request.get("body_file") or not request.get("path", "").startswith("/"):
            raise InvalidRun("request needs body_file and an absolute path")
        root = Path(__file__).resolve().parents[2]
        binary = os.environ.get("COMMITCUT_PROXY") or shutil.which("commitcut-proxy")
        if not binary:
            for candidate in [root / "target/release/commitcut-proxy", root / "target/debug/commitcut-proxy",
                              Path.cwd() / "target/release/commitcut-proxy", Path.cwd() / "target/debug/commitcut-proxy"]:
                if candidate.is_file():
                    binary = str(candidate)
                    break
        if not binary:
            raise InvalidRun("Build the native engine with cargo build --release, or set COMMITCUT_PROXY")
        psql = os.environ.get("COMMITCUT_PSQL") or shutil.which("psql")
        if not psql:
            raise InvalidRun("psql is required; set COMMITCUT_PSQL or add PostgreSQL bin to PATH")
        for assertion in data["assertions"]:
            if not all(k in assertion for k in ("name", "sql", "expected")):
                raise InvalidRun("Each assertion needs name, sql, and expected")
        return cls(path, data, database, binary, psql, include_sql)

    @property
    def base(self):
        return self.path.parent

    @property
    def timeout(self):
        return float(self.data.get("timeout_seconds", 10))

    def sql(self, *, query=None, file=None):
        args = [self.psql, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-d", self.database]
        args += ["-c", query] if query is not None else ["-f", str(file)]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InvalidRun("psql failed to execute within its deadline") from exc
        if result.returncode:
            # psql errors can contain SQL values. Keep full output in local run logs only.
            raise InvalidRun("Database reset/assertion failed; verify SQL and test database access")
        return result.stdout.strip()

    def reset(self):
        self.sql(file=self.base / self.data["reset_sql"])

    def check(self):
        results = []
        for spec in self.data["assertions"]:
            actual = self.sql(query=spec["sql"])
            expected = str(spec["expected"])
            results.append(dict(name=spec["name"], expected=expected, actual=actual, passed=actual == expected))
        return results


class OwnedProcess:
    def __init__(self, args, *, env=None, cwd=None, stdout=None, stderr=None, stdin=None):
        self.process = subprocess.Popen(args, env=env, cwd=cwd, stdout=stdout, stderr=stderr, stdin=stdin,
                                        text=True, start_new_session=True)
        self.stopped = False

    def kill(self):
        if self.stopped:
            return
        self.stopped = True
        # The process group is created by this object, never supplied by config.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)


class Proxy(OwnedProcess):
    def __init__(self, config, *, cut=0, phase="after", log_path):
        db = urllib.parse.urlsplit(config.database)
        host = db.hostname
        upstream = f"[{host}]:{db.port or 5432}" if ":" in host else f"{host}:{db.port or 5432}"
        args = [config.proxy, "--upstream", upstream, "--cut", str(cut), "--phase", phase]
        if config.include_sql:
            args.append("--include-sql")
        self.events = []
        self.condition = threading.Condition()
        self.closed = False
        self.log = log_path.open("w")
        super().__init__(args, stdout=subprocess.PIPE, stderr=self.log, stdin=subprocess.PIPE)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            ready = self.wait_event("ready", config.timeout)
            self.address = ready["address"]
        except BaseException:
            self.kill()
            raise

    def read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"event": "fatal", "message": "invalid native engine output"}
                with self.condition:
                    self.events.append(event)
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()

    def wait_event(self, kind, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event in self.events:
                    if event["event"] == "fatal":
                        raise InvalidRun("Native proxy: " + event["message"])
                    if event["event"] == kind:
                        return event
                if self.closed:
                    raise InvalidRun(f"Native proxy exited before {kind}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvalidRun(f"Timed out waiting for native proxy {kind}")
                self.condition.wait(min(remaining, 0.1))

    def arm(self, timeout):
        self.process.stdin.write("arm\n")
        self.process.stdin.flush()
        self.wait_event("armed", timeout)

    def verify(self):
        for event in self.events:
            if event["event"] == "fatal":
                raise InvalidRun("Native proxy: " + event["message"])
        if self.process.poll() is not None:
            raise InvalidRun("Native proxy exited unexpectedly")

    def kill(self):
        # Native proxy has no child processes. Avoid signalling a process-group
        # identifier after it exited (it can be reused by the OS).
        if not self.stopped:
            self.stopped = True
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in [self.process.stdin, self.process.stdout, self.log]:
            stream.close()


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def app_environment(config, proxy, port):
    db = urllib.parse.urlsplit(config.database)
    # Preserve percent-encoded credentials exactly; change only the endpoint.
    userinfo = db.netloc.rsplit("@", 1)[0] + "@" if "@" in db.netloc else ""
    query = urllib.parse.parse_qsl(db.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != "sslmode"] + [("sslmode", "disable")]
    proxied = urllib.parse.urlunsplit((db.scheme, userinfo + proxy.address, db.path, urllib.parse.urlencode(query), ""))
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in config.data["app"].get("env", {}).items()})
    env[config.data["app"].get("database_env", "DATABASE_URL")] = proxied
    env[config.data["app"].get("port_env", "PORT")] = str(port)
    return env


def start_app(config, proxy, directory, label):
    port = free_port()
    log = (directory / f"{label}.log").open("w")
    env = app_environment(config, proxy, port)
    try:
        app = OwnedProcess(config.data["app"]["command"], env=env,
                           cwd=config.base / config.data["app"].get("cwd", "."), stdout=log, stderr=log)
    except Exception:
        log.close()
        raise
    app.log = log
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + config.timeout
    try:
        while time.monotonic() < deadline:
            proxy.verify()
            if app.process.poll() is not None:
                raise InvalidRun(f"Application exited during startup; inspect {label}.log")
            try:
                with urllib.request.urlopen(url + config.data["app"].get("health_path", "/health"), timeout=.2) as r:
                    if 200 <= r.status < 300:
                        return app, url
            except (OSError, urllib.error.URLError):
                time.sleep(.02)
        raise InvalidRun("Application readiness deadline exceeded")
    except Exception:
        app.kill()
        log.close()
        raise


def stop_app(app):
    if app:
        app.kill()
        app.log.close()


def deliver(config, url):
    spec = config.data["request"]
    body = (config.base / spec["body_file"]).read_bytes()
    headers = {"Content-Type": "application/json", **spec.get("headers", {})}
    if "stripe_secret_env" in spec:
        secret = os.environ.get(spec["stripe_secret_env"]) or config.data["app"].get("env", {}).get(spec["stripe_secret_env"])
        if not secret:
            raise InvalidRun("Missing test webhook signing secret")
        timestamp = str(int(time.time()))
        digest = hmac.new(str(secret).encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers["Stripe-Signature"] = f"t={timestamp},v1={digest}"
    request = urllib.request.Request(url + spec["path"], data=body, headers=headers, method=spec.get("method", "POST"))
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            response.read(1_000_000)
            return {"status": response.status}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code}
    except (OSError, urllib.error.URLError) as exc:
        return {"status": None, "error": type(exc).__name__}


def fingerprint(boundary):
    return (boundary["index"], boundary["protocol"], boundary["query_sha256"], boundary["state_before"])


def validate_prefix(events, baseline, cut):
    begins = [e["boundary"] for e in events if e["event"] == "begin"]
    if len(begins) != cut:
        raise InvalidRun("Observed query count differs from selected baseline cut")
    for actual, expected in zip(begins, baseline[:cut]):
        if fingerprint(actual) != fingerprint(expected):
            raise InvalidRun("Query sequence drifted from baseline; cut is not reproducible")


def run_case(config, directory, *, cut=0, phase="after", baseline=None):
    directory.mkdir(parents=True, exist_ok=False)
    config.reset()
    proxy = None
    recovery_proxy = None
    app = None
    recovery = None
    request_thread = None
    result = {"cut": cut, "phase": phase if cut else "baseline", "status": "inconclusive"}
    try:
        proxy = Proxy(config, cut=cut, phase=phase, log_path=directory / "proxy.log")
        app, url = start_app(config, proxy, directory, "application")
        proxy.arm(config.timeout)
        if cut:
            request_thread = threading.Thread(target=deliver, args=(config, url), daemon=True)
            request_thread.start()
            cut_event = proxy.wait_event("cut", config.timeout)
            validate_prefix(proxy.events, baseline, cut)
            result["boundary"] = cut_event["boundary"]
            # Order matters: SIGKILL prevents exception cleanup; wait proves the
            # worker is gone before closing its frozen database connection.
            stop_app(app)
            result["application_exit"] = app.process.returncode
            app = None
            result["trace"] = list(proxy.events)
            proxy.kill()
            proxy = None
            request_thread.join(timeout=config.timeout + 1)
        else:
            first = deliver(config, url)
            proxy.verify()
            if not first["status"] or not 200 <= first["status"] < 300:
                raise InvalidRun(f"Baseline delivery was not accepted: {first['status']}")
            result["first_response"] = first
            result["trace"] = list(proxy.events)
            result["boundaries"] = [e for e in proxy.events if e["event"] == "boundary"]
            if not result["boundaries"]:
                raise InvalidRun("No database cycles observed; check application DATABASE_URL")
            initial = config.check()
            if not all(a["passed"] for a in initial):
                result["assertions"] = initial
                raise InvalidRun("Unfaulted baseline violates business invariants")
            stop_app(app)
            app = None
            proxy.kill()
            proxy = None
        # A separate proxy ensures no fault survives into recovery. Keep DB
        # state, start a fresh process and re-sign exactly the same fixture.
        recovery_proxy = Proxy(config, log_path=directory / "recovery-proxy.log")
        recovery, recovery_url = start_app(config, recovery_proxy, directory, "recovery")
        recovery_proxy.arm(config.timeout)
        responses = [deliver(config, recovery_url) for _ in range(int(config.data.get("redeliveries", 2)))]
        recovery_proxy.verify()
        result["recovery_responses"] = responses
        if any(not r["status"] or not 200 <= r["status"] < 300 for r in responses):
            raise InvalidRun("Recovery delivery was not accepted; no trustworthy fulfillment result")
        result["assertions"] = config.check()
        result["recovery_trace"] = list(recovery_proxy.events)
        result["status"] = "pass" if all(a["passed"] for a in result["assertions"]) else "violation"
    except (InvalidRun, OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
        if proxy:
            result["trace"] = list(proxy.events)
    finally:
        stop_app(app)
        stop_app(recovery)
        if proxy:
            proxy.kill()
        if recovery_proxy:
            recovery_proxy.kill()
        if request_thread:
            request_thread.join(timeout=1)
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


@contextlib.contextmanager
def database_lease(database):
    # Tests reset state. A second local CommitCut run must not reset this one's
    # database mid-test. This coordinates CommitCut runners, not other clients.
    url = urllib.parse.urlsplit(database)
    key = f"loopback:{url.port or 5432}/{urllib.parse.unquote(url.path)}"
    name = "commitcut-" + hashlib.sha256(key.encode()).hexdigest()[:24] + ".lock"
    with (Path(tempfile.gettempdir()) / name).open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InvalidRun("Another CommitCut run owns this test database") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_matrix(config, output, *, selected=None, phase=None):
    with database_lease(config.database):
        return _run_matrix(config, output, selected=selected, phase=phase)


def _run_matrix(config, output, *, selected=None, phase=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    baseline = run_case(config, output / "baseline")
    report = {"format_version": 1, "name": config.data["name"], "scope": "observed PostgreSQL query-cycle boundaries; application SIGKILL, database remains running",
              "baseline": baseline, "cases": [], "status": "inconclusive"}
    if baseline["status"] == "pass":
        boundaries = baseline["boundaries"]
        indices = [selected] if selected else range(1, len(boundaries) + 1)
        if selected and selected > len(boundaries):
            raise InvalidRun("Selected cut exceeds observed baseline")
        for cut in indices:
            for timing in ([phase] if phase else ["before", "after"]):
                case = run_case(config, output / f"cut-{cut:03d}-{timing}", cut=cut, phase=timing, baseline=boundaries)
                report["cases"].append(case)
                print(f"  {cut:>3} {timing:<6} {case['status']}", flush=True)
        statuses = [c["status"] for c in report["cases"]]
        report["status"] = "inconclusive" if "inconclusive" in statuses else ("violation" if "violation" in statuses else "pass")
        report["coverage"] = {"observed_cycles": len(boundaries), "tested_cuts": len(statuses), "possible_cuts": len(boundaries)*2,
                              "passing": statuses.count("pass"), "violations": statuses.count("violation"), "inconclusive": statuses.count("inconclusive")}
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    from .report import write_html
    write_html([report], output / "report.html")
    return report
