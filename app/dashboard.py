#!/usr/bin/python3
"""Dashboard, system-status API, Pi list manager, and fan-curve API."""

import ipaddress
import json
import os
import re
import socket

import psutil
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.environ.get("PIFAN_BASE", "/opt/pifandashboard")
WEB_DIR = os.path.join(BASE_DIR, "web")
PI_LIST_FILE = os.environ.get("PIFAN_PI_LIST", "/var/lib/pifandashboard/pi_list.json")
CONFIG_FILE = os.environ.get("PIFAN_CONFIG", "/etc/pifandashboard/fan.json")
STATUS_FILE = os.environ.get("PIFAN_STATUS", "/run/pifandashboard/status.json")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


@app.after_request
def allow_status_reads(response):
    # A dashboard can monitor another Pi without opening configuration writes.
    if request.path == "/status" and request.method == "GET":
        response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def local_client():
    try:
        address = ipaddress.ip_address(request.remote_addr)
        return address.is_private or address.is_loopback
    except ValueError:
        return False


def read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def write_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def valid_pi(data):
    try:
        return (
            NAME_PATTERN.fullmatch(str(data.get("name", ""))) is not None
            and ipaddress.ip_address(data.get("ip", "")).version == 4
            and 1024 <= int(data.get("port", 0)) <= 65535
        )
    except (ValueError, TypeError):
        return False


def normalize_config(data):
    try:
        config = {
            "gpio": int(data.get("gpio", 14)),
            "start_temp": float(data["start_temp"]),
            "full_temp": float(data["full_temp"]),
            "min_duty": int(data["min_duty"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not (2 <= config["gpio"] <= 27):
        return None
    if not (20 <= config["start_temp"] <= 75):
        return None
    if not (config["start_temp"] + 5 <= config["full_temp"] <= 90):
        return None
    if not (20 <= config["min_duty"] <= 100):
        return None
    return config


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/status")
def status():
    fan = read_json(STATUS_FILE, {})
    if "temperature" not in fan:
        return jsonify(error="fan controller has not reported yet"), 503
    return jsonify(
        temperature=fan["temperature"],
        speed=fan["speed"],
        cpu=psutil.cpu_percent(interval=0.2),
        memory=psutil.virtual_memory().percent,
        hostname=socket.gethostname(),
    )


@app.get("/get_pi_list")
def get_pi_list():
    return jsonify(read_json(PI_LIST_FILE, []))


@app.post("/add_pi")
def add_pi():
    if not local_client():
        return jsonify(error="local network only"), 403
    data = request.get_json(silent=True) or {}
    if not valid_pi(data):
        return jsonify(error="invalid Pi details"), 400
    entry = {"name": data["name"], "ip": data["ip"], "port": int(data["port"])}
    pis = read_json(PI_LIST_FILE, [])
    if any(pi["ip"] == entry["ip"] for pi in pis):
        return jsonify(error="IP already exists"), 409
    pis.append(entry)
    write_json(PI_LIST_FILE, pis)
    return jsonify(status="added"), 201


@app.post("/edit_pi")
def edit_pi():
    if not local_client():
        return jsonify(error="local network only"), 403
    data = request.get_json(silent=True) or {}
    replacement = {"name": data.get("name"), "ip": data.get("ip"), "port": data.get("port")}
    if not data.get("originalIp") or not valid_pi(replacement):
        return jsonify(error="invalid Pi details"), 400
    replacement["port"] = int(replacement["port"])
    pis = read_json(PI_LIST_FILE, [])
    for index, pi in enumerate(pis):
        if pi["ip"] == data["originalIp"]:
            pis[index] = replacement
            write_json(PI_LIST_FILE, pis)
            return jsonify(status="updated")
    return jsonify(error="original IP not found"), 404


@app.post("/delete_pi")
def delete_pi():
    if not local_client():
        return jsonify(error="local network only"), 403
    target = (request.get_json(silent=True) or {}).get("ip")
    pis = [pi for pi in read_json(PI_LIST_FILE, []) if pi.get("ip") != target]
    write_json(PI_LIST_FILE, pis)
    return jsonify(status="deleted")


@app.get("/fan-config")
def get_fan_config():
    return jsonify(read_json(CONFIG_FILE, {}))


@app.put("/fan-config")
def put_fan_config():
    if not local_client():
        return jsonify(error="local network only"), 403
    config = normalize_config(request.get_json(silent=True) or {})
    if config is None:
        return jsonify(error="invalid fan curve"), 400
    write_json(CONFIG_FILE, config)
    return jsonify(config)


if __name__ == "__main__":
    port = int(os.environ.get("PIFAN_PORT", "8088"))
    app.run(host="0.0.0.0", port=port, debug=False)
