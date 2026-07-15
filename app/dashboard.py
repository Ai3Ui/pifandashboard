#!/usr/bin/python3
"""Authenticated dashboard and low-overhead Raspberry Pi status API."""

import collections
import concurrent.futures
import functools
import ipaddress
import json
import os
import socket
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

from app.auth import SessionStore
from app.config import atomic_write_json, normalize_fan_config, read_fan_config
from app.state import MAX_PIS, PRIVATE_NETWORKS, normalize_pi, normalize_pi_list, read_pi_list

BASE_DIR = os.environ.get("PIFAN_BASE", "/opt/pifandashboard")
WEB_DIR = os.path.join(BASE_DIR, "web")
DATA_DIR = os.environ.get("PIFAN_DATA", "/var/lib/pifandashboard")
PI_LIST_FILE = os.environ.get("PIFAN_PI_LIST", os.path.join(DATA_DIR, "web", "pi_list.json"))
CONFIG_FILE = os.environ.get("PIFAN_CONFIG", os.path.join(DATA_DIR, "config", "fan.json"))
AUTH_FILE = os.environ.get("PIFAN_AUTH", "/etc/pifandashboard/auth.json")
STATUS_FILE = os.environ.get("PIFAN_STATUS", "/run/pifandashboard/status.json")
MAX_JSON_BYTES = 16 * 1024

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BYTES
sessions = SessionStore(AUTH_FILE)
data_lock = threading.RLock()


class RateLimiter:
    def __init__(self):
        self.requests = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, key, limit, window):
        now = time.monotonic()
        cutoff = now - window
        with self.lock:
            events = self.requests[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if len(self.requests) > 1024:
                for old_key in [item for item, queue in self.requests.items() if not queue or queue[-1] < cutoff]:
                    self.requests.pop(old_key, None)
            return True


limiter = RateLimiter()


class SystemMetrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_read = 0.0
        try:
            self.last_cpu = self._cpu_times()
        except OSError:
            self.last_cpu = (0, 0)
        self.values = {"cpu": 0.0, "memory": 0.0, "disk": 0.0, "uptime": 0, "load": 0.0}

    @staticmethod
    def _cpu_times():
        with open("/proc/stat", encoding="ascii") as handle:
            values = [int(value) for value in handle.readline().split()[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    @staticmethod
    def _memory_percent():
        values = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(value.split()[0])
        return round((values["MemTotal"] - values["MemAvailable"]) * 100 / values["MemTotal"], 1)

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            if now - self.last_read < 1:
                return self.values.copy()
            total, idle = self._cpu_times()
            previous_total, previous_idle = self.last_cpu
            total_delta = total - previous_total
            idle_delta = idle - previous_idle
            cpu = 0.0 if total_delta <= 0 else (total_delta - idle_delta) * 100 / total_delta
            disk = os.statvfs("/")
            disk_total = disk.f_blocks * disk.f_frsize
            disk_available = disk.f_bavail * disk.f_frsize
            with open("/proc/uptime", encoding="ascii") as handle:
                uptime = int(float(handle.read().split()[0]))
            self.values = {
                "cpu": round(max(0.0, min(cpu, 100.0)), 1),
                "memory": self._memory_percent(),
                "disk": round((disk_total - disk_available) * 100 / disk_total, 1),
                "uptime": uptime,
                "load": round(os.getloadavg()[0], 2),
            }
            self.last_cpu = total, idle
            self.last_read = now
            return self.values.copy()


metrics = SystemMetrics()
fleet_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="pifan-status")
fleet_lock = threading.Lock()
fleet_refresh_lock = threading.Lock()
fleet_cache = {"key": None, "updated": 0.0, "results": []}


def remote_address():
    try:
        return ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return None


def local_client():
    address = remote_address()
    return bool(address and address.version == 4 and any(address in network for network in PRIVATE_NETWORKS))


def bearer_token():
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    return token if separator and scheme.lower() == "bearer" else ""


def require_session(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if not local_client():
            return jsonify(error="private network only"), 403
        if not sessions.valid(bearer_token()):
            return jsonify(error="authentication required"), 401
        return function(*args, **kwargs)

    return wrapped


def json_object():
    if not request.is_json:
        return None
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def valid_pi(data):
    return normalize_pi(data) is not None


@app.before_request
def rate_limit_request():
    address = request.remote_addr or "unknown"
    if request.path == "/auth/login":
        allowed = limiter.allow((address, "login"), 8, 300)
    elif request.path == "/fleet-status":
        allowed = limiter.allow((address, "fleet"), 15, 60)
    elif request.path == "/status":
        allowed = limiter.allow((address, "status"), 180, 60)
    else:
        allowed = limiter.allow((address, "general"), 120, 60)
    if not allowed:
        return jsonify(error="too many requests"), 429
    return None


@app.after_request
def secure_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    if request.path.startswith(
        ("/auth/", "/status", "/fleet-status", "/fan-config", "/get_pi_list", "/add_pi", "/edit_pi", "/delete_pi")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="request body is too large"), 413


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="ok")


@app.route("/status", methods=["GET"])
def status():
    if not local_client():
        return jsonify(error="private network only"), 403
    try:
        fan = read_json(STATUS_FILE)
        status_age = time.time() - float(fan.get("updated_at", 0))
        if "temperature" not in fan or status_age > 20:
            raise ValueError("fan status is stale")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return jsonify(error=str(error)), 503
    return jsonify(
        temperature=fan["temperature"],
        speed=fan["speed"],
        gpio=fan["gpio"],
        curve=fan.get("curve", {}),
        controller_error=fan.get("error"),
        hostname=socket.gethostname(),
        **metrics.snapshot(),
    )


def clean_remote_status(value):
    if not isinstance(value, dict):
        raise ValueError("invalid status response")
    numeric_ranges = {
        "temperature": (-20, 150),
        "speed": (0, 100),
        "cpu": (0, 100),
        "memory": (0, 100),
        "disk": (0, 100),
        "load": (0, 1000),
        "uptime": (0, 10**10),
    }
    result = {}
    for key, (minimum, maximum) in numeric_ranges.items():
        number = float(value.get(key, 0))
        if not minimum <= number <= maximum:
            raise ValueError(f"invalid {key}")
        result[key] = round(number, 1) if key != "uptime" else int(number)
    result["hostname"] = str(value.get("hostname", "Raspberry Pi"))[:64]
    curve = value.get("curve", {})
    validated_curve = normalize_fan_config({**curve, "gpio": value.get("gpio", 14)})
    if validated_curve is None:
        raise ValueError("invalid remote fan curve")
    result["gpio"] = validated_curve.pop("gpio")
    result["curve"] = validated_curve
    error = value.get("controller_error")
    result["controller_error"] = str(error)[:200] if error else None
    return result


def fetch_pi_status(pi):
    deadline = time.monotonic() + 2.0
    body = b""
    try:
        with socket.create_connection((pi["ip"], int(pi["port"])), timeout=2.0) as peer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("status request exceeded two seconds")
            peer.settimeout(remaining)
            peer.sendall(
                b"GET /status HTTP/1.1\r\nHost: dashboard-peer\r\n"
                b"Accept: application/json\r\nConnection: close\r\n\r\n"
            )
            response = b""
            header_end = -1
            content_length = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("status request exceeded two seconds")
                peer.settimeout(remaining)
                chunk = peer.recv(4096)
                if not chunk:
                    break
                response += chunk
                if header_end < 0:
                    header_end = response.find(b"\r\n\r\n")
                    if header_end < 0 and len(response) > 8192:
                        raise ValueError("status headers are too large")
                    if header_end >= 0:
                        if header_end > 8192:
                            raise ValueError("status headers are too large")
                        header_block = response[:header_end].decode("iso-8859-1")
                        lines = header_block.split("\r\n")
                        if not lines[0].startswith("HTTP/1.") or " 200 " not in lines[0]:
                            raise ValueError("peer returned a non-success status")
                        headers = {}
                        for line in lines[1:]:
                            name, separator, value = line.partition(":")
                            if not separator:
                                raise ValueError("malformed status headers")
                            headers[name.strip().lower()] = value.strip()
                        if "transfer-encoding" in headers:
                            raise ValueError("unsupported status transfer encoding")
                        content_length = int(headers.get("content-length", "-1"))
                        if not 0 <= content_length <= 32 * 1024:
                            raise ValueError("invalid status content length")
                if header_end >= 0 and len(response) - header_end - 4 >= content_length:
                    break
                if len(response) > 8192 + 4 + 32 * 1024:
                    raise ValueError("status response is too large")
            if header_end < 0 or content_length is None:
                raise ValueError("incomplete status response")
            body = response[header_end + 4 :]
            if len(body) != content_length:
                raise ValueError("incomplete status body")
        data = clean_remote_status(json.loads(body))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, json.JSONDecodeError) as error:
        data = {"error": f"Unavailable: {str(error)[:100]}"}
    return {**pi, "data": data}


@app.route("/fleet-status", methods=["GET"])
@require_session
def fleet_status():
    try:
        with data_lock:
            pis = read_pi_list(PI_LIST_FILE)
    except (OSError, ValueError, json.JSONDecodeError):
        return jsonify(error="Pi list is unavailable or invalid"), 500
    key = tuple((pi["name"], pi["ip"], int(pi["port"])) for pi in pis)
    now = time.monotonic()
    with fleet_lock:
        if fleet_cache["key"] == key and now - fleet_cache["updated"] < 5:
            return jsonify(fleet_cache["results"])
    if not fleet_refresh_lock.acquire(blocking=False):
        with fleet_lock:
            if fleet_cache["key"] == key:
                return jsonify(fleet_cache["results"])
        return jsonify(error="fleet refresh is already in progress"), 503
    try:
        results = list(fleet_executor.map(fetch_pi_status, pis))
    finally:
        fleet_refresh_lock.release()
    with fleet_lock:
        fleet_cache.update(key=key, updated=time.monotonic(), results=results)
    return jsonify(results)


@app.route("/auth/login", methods=["POST"])
def login():
    if not local_client():
        return jsonify(error="private network only"), 403
    data = json_object()
    password = data.get("password") if data else None
    try:
        token = sessions.login(password)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        token = None
    if token is None:
        time.sleep(0.25)
        return jsonify(error="invalid password"), 401
    return jsonify(token=token, expires_in=sessions.ttl)


@app.route("/auth/check", methods=["GET"])
@require_session
def check_auth():
    return jsonify(authenticated=True)


@app.route("/auth/logout", methods=["POST"])
@require_session
def logout():
    sessions.logout(bearer_token())
    return jsonify(status="logged out")


@app.route("/get_pi_list", methods=["GET"])
@require_session
def get_pi_list():
    try:
        with data_lock:
            value = read_pi_list(PI_LIST_FILE)
        return jsonify(value)
    except (OSError, ValueError, json.JSONDecodeError):
        return jsonify(error="Pi list is unavailable or invalid"), 500


@app.route("/add_pi", methods=["POST"])
@require_session
def add_pi():
    data = json_object()
    entry = normalize_pi(data)
    if entry is None:
        return jsonify(error="invalid Pi details"), 400
    try:
        with data_lock:
            pis = read_pi_list(PI_LIST_FILE)
            if len(pis) >= MAX_PIS:
                return jsonify(error=f"maximum of {MAX_PIS} Pis reached"), 409
            if any(pi["ip"] == entry["ip"] for pi in pis):
                return jsonify(error="IP already exists"), 409
            if any(pi["name"].casefold() == entry["name"].casefold() for pi in pis):
                return jsonify(error="name already exists"), 409
            pis.append(entry)
            atomic_write_json(PI_LIST_FILE, normalize_pi_list(pis))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return jsonify(error="could not update Pi list"), 500
    return jsonify(status="added"), 201


@app.route("/edit_pi", methods=["POST"])
@require_session
def edit_pi():
    data = json_object()
    candidate = {
        "name": data.get("name") if data else None,
        "ip": data.get("ip") if data else None,
        "port": data.get("port") if data else None,
    }
    original = data.get("originalIp") if data else None
    replacement = normalize_pi(candidate)
    if not original or replacement is None:
        return jsonify(error="invalid Pi details"), 400
    try:
        with data_lock:
            pis = read_pi_list(PI_LIST_FILE)
            if any(pi["ip"] == replacement["ip"] and pi["ip"] != original for pi in pis):
                return jsonify(error="IP already exists"), 409
            if any(
                pi["name"].casefold() == replacement["name"].casefold() and pi["ip"] != original
                for pi in pis
            ):
                return jsonify(error="name already exists"), 409
            for index, pi in enumerate(pis):
                if pi["ip"] == original:
                    pis[index] = replacement
                    atomic_write_json(PI_LIST_FILE, normalize_pi_list(pis))
                    return jsonify(status="updated")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return jsonify(error="could not update Pi list"), 500
    return jsonify(error="original IP not found"), 404


@app.route("/delete_pi", methods=["POST"])
@require_session
def delete_pi():
    data = json_object()
    target = data.get("ip") if data else None
    try:
        address = ipaddress.ip_address(target or "")
    except ValueError:
        return jsonify(error="invalid IP"), 400
    if address.version != 4:
        return jsonify(error="invalid IP"), 400
    try:
        with data_lock:
            pis = read_pi_list(PI_LIST_FILE)
            filtered = [pi for pi in pis if pi.get("ip") != target]
            if len(filtered) == len(pis):
                return jsonify(error="IP not found"), 404
            atomic_write_json(PI_LIST_FILE, filtered)
    except (OSError, TypeError, json.JSONDecodeError):
        return jsonify(error="could not update Pi list"), 500
    return jsonify(status="deleted")


@app.route("/fan-config", methods=["GET"])
@require_session
def get_fan_config():
    try:
        return jsonify(read_fan_config(CONFIG_FILE))
    except (OSError, ValueError, json.JSONDecodeError):
        return jsonify(error="fan configuration is invalid"), 500


@app.route("/fan-config", methods=["PUT"])
@require_session
def put_fan_config():
    config = normalize_fan_config(json_object())
    if config is None:
        return jsonify(error="invalid fan curve"), 400
    try:
        with data_lock:
            current = read_fan_config(CONFIG_FILE)
            if config["gpio"] != current["gpio"]:
                return jsonify(error="GPIO changes require reinstalling with --gpio"), 409
            atomic_write_json(CONFIG_FILE, config)
    except (OSError, ValueError, json.JSONDecodeError):
        return jsonify(error="could not save fan curve"), 500
    return jsonify(config)
