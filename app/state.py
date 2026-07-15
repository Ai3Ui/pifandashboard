#!/usr/bin/python3
"""Validated, atomic storage helpers for the dashboard's managed Pi list."""

import argparse
import ipaddress
import json
import re

from app.config import atomic_write_json

MAX_PIS = 16
NAME_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)


def private_ipv4(value):
    """Return a canonical RFC1918/loopback IPv4 address, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None
    if address.version != 4 or not any(address in network for network in PRIVATE_NETWORKS):
        return None
    return str(address)


def normalize_pi(value):
    """Validate an untrusted Pi-list entry and return its canonical form."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    address = private_ipv4(value.get("ip"))
    raw_port = value.get("port")
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None or address is None:
        return None
    if isinstance(raw_port, bool) or not isinstance(raw_port, (int, str)):
        return None
    if isinstance(raw_port, str) and (
        len(raw_port) > 5 or not raw_port.isascii() or not raw_port.isdigit()
    ):
        return None
    port = int(raw_port)
    if not 1024 <= port <= 65535:
        return None
    return {"name": name, "ip": address, "port": port}


def normalize_pi_list(value):
    """Validate and canonicalise the complete list, including uniqueness."""
    if not isinstance(value, list) or len(value) > MAX_PIS:
        raise ValueError(f"Pi list must be an array of at most {MAX_PIS} entries")
    result = []
    names = set()
    addresses = set()
    for item in value:
        clean = normalize_pi(item)
        if clean is None:
            raise ValueError("Pi list contains an invalid entry")
        folded_name = clean["name"].casefold()
        if clean["ip"] in addresses or folded_name in names:
            raise ValueError("Pi list contains a duplicate name or IP address")
        names.add(folded_name)
        addresses.add(clean["ip"])
        result.append(clean)
    return result


def read_pi_list(path):
    with open(path, encoding="utf-8") as handle:
        return normalize_pi_list(json.load(handle))


def update_self(path, address, port, hostname):
    """Add or replace this dashboard's own record without discarding bad state."""
    try:
        items = read_pi_list(path)
    except FileNotFoundError:
        items = []
    replacement = normalize_pi({"name": hostname, "ip": address, "port": port})
    if replacement is None:
        raise ValueError("local dashboard details are invalid")
    filtered = [
        item
        for item in items
        if item["ip"] != replacement["ip"] and item["name"].casefold() != replacement["name"].casefold()
    ]
    if len(filtered) >= MAX_PIS:
        raise ValueError(f"Pi list already contains {MAX_PIS} other entries")
    filtered.append(replacement)
    atomic_write_json(path, filtered)


def main():
    parser = argparse.ArgumentParser(description="Validate and update Pi Fan Dashboard state")
    parser.add_argument("path")
    parser.add_argument("--update-self", action="store_true")
    parser.add_argument("--ip")
    parser.add_argument("--port")
    parser.add_argument("--name")
    arguments = parser.parse_args()
    if arguments.update_self:
        update_self(arguments.path, arguments.ip, arguments.port, arguments.name)
    else:
        read_pi_list(arguments.path)


if __name__ == "__main__":
    main()
