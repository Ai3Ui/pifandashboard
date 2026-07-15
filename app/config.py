#!/usr/bin/python3
"""Shared fan-configuration validation and safe file helpers."""

import argparse
import json
import math
import os
import tempfile

ALLOWED_GPIOS = (12, 13, 14, 18, 19)
DEFAULT_CONFIG = {
    "gpio": 14,
    "start_temp": 45.0,
    "full_temp": 75.0,
    "min_duty": 45,
    "hysteresis": 2.0,
}


def exact_integer(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer setting")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("setting must be an integer")
        return int(value)
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 3:
        return int(value)
    raise ValueError("setting must be an integer")


def normalize_fan_config(data):
    if not isinstance(data, dict):
        return None
    try:
        config = {
            "gpio": exact_integer(data.get("gpio", DEFAULT_CONFIG["gpio"])),
            "start_temp": float(data["start_temp"]),
            "full_temp": float(data["full_temp"]),
            "min_duty": exact_integer(data["min_duty"]),
            "hysteresis": float(data.get("hysteresis", DEFAULT_CONFIG["hysteresis"])),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(config[key]) for key in ("start_temp", "full_temp", "hysteresis")):
        return None
    if config["gpio"] not in ALLOWED_GPIOS:
        return None
    if not 20 <= config["start_temp"] <= 75:
        return None
    if not config["start_temp"] + 5 <= config["full_temp"] <= 90:
        return None
    if not 35 <= config["min_duty"] <= 100:
        return None
    if not 0 <= config["hysteresis"] <= 10:
        return None
    return config


def read_fan_config(path):
    with open(path, encoding="utf-8") as handle:
        config = normalize_fan_config(json.load(handle))
    if config is None:
        raise ValueError(f"invalid fan configuration in {path}")
    return config


def atomic_write_json(path, value, mode=0o640):
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".pifan-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description="Validate or create a Pi Fan Dashboard curve")
    parser.add_argument("path")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--patch", action="store_true")
    action.add_argument("--canonicalize", action="store_true")
    parser.add_argument("--gpio", type=int)
    parser.add_argument("--start-temp", type=float)
    parser.add_argument("--full-temp", type=float)
    parser.add_argument("--min-duty", type=int)
    parser.add_argument("--hysteresis", type=float)
    arguments = parser.parse_args()
    supplied = {
        key: value
        for key, value in {
            "gpio": arguments.gpio,
            "start_temp": arguments.start_temp,
            "full_temp": arguments.full_temp,
            "min_duty": arguments.min_duty,
            "hysteresis": arguments.hysteresis,
        }.items()
        if value is not None
    }
    if arguments.canonicalize:
        atomic_write_json(arguments.path, read_fan_config(arguments.path))
    elif arguments.write or arguments.patch:
        if arguments.patch and not supplied:
            parser.error("--patch requires at least one curve option")
        base = read_fan_config(arguments.path) if arguments.patch else DEFAULT_CONFIG.copy()
        config = normalize_fan_config({**base, **supplied})
        if config is None:
            parser.error("fan curve values are outside the safe ranges")
        atomic_write_json(arguments.path, config)
    else:
        read_fan_config(arguments.path)


if __name__ == "__main__":
    main()
