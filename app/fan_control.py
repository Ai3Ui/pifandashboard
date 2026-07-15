#!/usr/bin/python3
"""PWM fan controller with a reloadable JSON fan curve."""

import json
import os
import signal
import time

import RPi.GPIO as GPIO

CONFIG_FILE = os.environ.get("PIFAN_CONFIG", "/etc/pifandashboard/fan.json")
STATUS_FILE = os.environ.get("PIFAN_STATUS", "/run/pifandashboard/status.json")
PWM_FREQUENCY = 100
DEFAULT_CONFIG = {"gpio": 14, "start_temp": 45.0, "full_temp": 75.0, "min_duty": 45}


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as handle:
            config = {**DEFAULT_CONFIG, **json.load(handle)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        config = DEFAULT_CONFIG.copy()
    return config


def cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", encoding="ascii") as handle:
        return int(handle.read().strip()) / 1000.0


def requested_duty(temp, config):
    start = float(config["start_temp"])
    full = float(config["full_temp"])
    minimum = int(config["min_duty"])
    if temp < start:
        return 0
    if temp >= full:
        return 100
    fraction = (temp - start) / (full - start)
    return round(minimum + fraction * (100 - minimum))


def write_status(temp, duty, config):
    temporary = STATUS_FILE + ".tmp"
    payload = {
        "temperature": round(temp, 1),
        "speed": duty,
        "gpio": int(config["gpio"]),
    }
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.chmod(temporary, 0o644)
    os.replace(temporary, STATUS_FILE)


def main():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    config = load_config()
    gpio = int(config["gpio"])
    GPIO.setup(gpio, GPIO.OUT, initial=GPIO.LOW)
    fan = GPIO.PWM(gpio, PWM_FREQUENCY)
    fan.start(0)
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            current = load_config()
            if int(current["gpio"]) != gpio:
                # A GPIO change is applied after the service is restarted by the API.
                current["gpio"] = gpio
            temperature = cpu_temperature()
            duty = requested_duty(temperature, current)
            fan.ChangeDutyCycle(duty)
            write_status(temperature, duty, current)
            time.sleep(5)
    finally:
        fan.ChangeDutyCycle(100)
        fan.stop()
        GPIO.cleanup(gpio)


if __name__ == "__main__":
    main()
