#!/usr/bin/python3
"""Fail-safe PWM fan controller with a validated, reloadable curve."""

import json
import logging
import os
import signal
import tempfile
import threading
import time

import RPi.GPIO as GPIO

from app.config import ALLOWED_GPIOS, DEFAULT_CONFIG, read_fan_config

CONFIG_FILE = os.environ.get("PIFAN_CONFIG", "/var/lib/pifandashboard/config/fan.json")
STATUS_FILE = os.environ.get("PIFAN_STATUS", "/run/pifandashboard/status.json")
PWM_FREQUENCY = 100
POLL_INTERVAL = 5
START_BOOST_SECONDS = 0.6
ACTIVE_GPIO = int(os.environ.get("PIFAN_GPIO", str(DEFAULT_CONFIG["gpio"])))
if ACTIVE_GPIO not in ALLOWED_GPIOS:
    raise ValueError(f"PIFAN_GPIO must be one of {ALLOWED_GPIOS}")
FAILSAFE_CONFIG = {
    **DEFAULT_CONFIG,
    "start_temp": 20.0,
    "full_temp": 25.0,
    "min_duty": 100,
    "hysteresis": 0.0,
    "_force_full_speed": True,
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("pifandashboard-fan")


def cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", encoding="ascii") as handle:
        return int(handle.read().strip()) / 1000.0


def requested_duty(temp, config, was_running=False):
    if config.get("_force_full_speed"):
        return 100
    start = config["start_temp"]
    full = config["full_temp"]
    minimum = config["min_duty"]
    stop = start - config["hysteresis"]
    if (was_running and temp < stop) or (not was_running and temp < start):
        return 0
    if temp >= full:
        return 100
    effective_temp = max(temp, start)
    fraction = (effective_temp - start) / (full - start)
    return round(minimum + fraction * (100 - minimum))


def write_status(temp, duty, config, error=None):
    payload = {
        "temperature": round(temp, 1),
        "speed": int(duty),
        "gpio": int(config["gpio"]),
        "curve": {
            "start_temp": config["start_temp"],
            "full_temp": config["full_temp"],
            "min_duty": config["min_duty"],
            "hysteresis": config["hysteresis"],
        },
        "updated_at": time.time(),
        "error": error,
    }
    directory = os.path.dirname(STATUS_FILE)
    descriptor, temporary = tempfile.mkstemp(prefix=".fan-status-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, STATUS_FILE)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def initial_config():
    try:
        config = read_fan_config(CONFIG_FILE)
        config["gpio"] = ACTIVE_GPIO
        return config
    except (OSError, ValueError, json.JSONDecodeError) as error:
        log.error("Invalid startup configuration; using full-speed fail-safe: %s", error)
        config = FAILSAFE_CONFIG.copy()
        config["gpio"] = ACTIVE_GPIO
        return config


def main():
    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config = initial_config()
    gpio = int(config["gpio"])
    fan = None
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(gpio, GPIO.OUT, initial=GPIO.HIGH)
        fan = GPIO.PWM(gpio, PWM_FREQUENCY)
        fan.start(100)
        previous_duty = 100
        last_valid_config = config
        log.info("Controlling GPIO%d at %d Hz", gpio, PWM_FREQUENCY)

        while not stop_event.is_set():
            error_message = None
            try:
                candidate = read_fan_config(CONFIG_FILE)
                if candidate["gpio"] != gpio:
                    error_message = f"GPIO change to {candidate['gpio']} awaits service restart"
                    candidate["gpio"] = gpio
                last_valid_config = candidate
            except (OSError, ValueError, json.JSONDecodeError) as error:
                error_message = f"invalid config; retaining last valid curve: {error}"
                log.warning(error_message)
            try:
                temperature = cpu_temperature()
                duty = requested_duty(temperature, last_valid_config, previous_duty > 0)
                if previous_duty == 0 and duty > 0:
                    fan.ChangeDutyCycle(100)
                    if stop_event.wait(START_BOOST_SECONDS):
                        break
                fan.ChangeDutyCycle(duty)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                temperature = -1.0
                duty = 100
                error_message = f"controller fail-safe: {error}"
                log.error(error_message)
                try:
                    fan.ChangeDutyCycle(100)
                except RuntimeError as gpio_error:
                    log.critical("Unable to command fail-safe duty: %s", gpio_error)
                    raise
            previous_duty = duty
            try:
                write_status(temperature, duty, last_valid_config, error_message)
            except OSError as error:
                log.error("Unable to write status: %s", error)
            stop_event.wait(POLL_INTERVAL)
    finally:
        log.info("Stopping controller; leaving the control pin high for full-speed fail-safe")
        if fan is not None:
            try:
                fan.ChangeDutyCycle(100)
                time.sleep(0.2)
            except RuntimeError as error:
                log.error("Unable to command final full duty: %s", error)
            try:
                fan.stop()
            except RuntimeError:
                pass
        try:
            GPIO.output(gpio, GPIO.HIGH)
        except RuntimeError as error:
            log.error("Unable to leave GPIO high: %s", error)


if __name__ == "__main__":
    main()
