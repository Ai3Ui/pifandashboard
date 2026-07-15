import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


gpio = types.ModuleType("RPi.GPIO")
rpi = types.ModuleType("RPi")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

fan_control = importlib.import_module("app.fan_control")


class FanCurveTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "gpio": 14,
            "start_temp": 45.0,
            "full_temp": 75.0,
            "min_duty": 45,
            "hysteresis": 2.0,
        }

    def test_fan_is_off_below_start_temperature(self):
        self.assertEqual(fan_control.requested_duty(44.9, self.config), 0)

    def test_fan_starts_at_minimum_duty(self):
        self.assertEqual(fan_control.requested_duty(45, self.config), 45)

    def test_fan_reaches_full_duty(self):
        self.assertEqual(fan_control.requested_duty(75, self.config), 100)

    def test_fan_curve_is_linear(self):
        self.assertEqual(fan_control.requested_duty(60, self.config), 72)

    def test_hysteresis_prevents_chatter(self):
        self.assertEqual(fan_control.requested_duty(44, self.config, was_running=True), 45)
        self.assertEqual(fan_control.requested_duty(42.9, self.config, was_running=True), 0)

    def test_pwm_constructor_failure_leaves_pin_high(self):
        calls = []
        gpio.BCM = "BCM"
        gpio.OUT = "OUT"
        gpio.HIGH = 1
        gpio.setwarnings = lambda _value: None
        gpio.setmode = lambda _value: None
        gpio.setup = lambda pin, mode, initial: calls.append(("setup", pin, mode, initial))
        gpio.output = lambda pin, value: calls.append(("output", pin, value))
        gpio.PWM = mock.Mock(side_effect=RuntimeError("PWM unavailable"))

        with mock.patch.object(fan_control, "initial_config", return_value=self.config):
            with self.assertRaisesRegex(RuntimeError, "PWM unavailable"):
                fan_control.main()

        self.assertIn(("setup", 14, "OUT", 1), calls)
        self.assertEqual(calls[-1], ("output", 14, 1))

    def test_invalid_startup_config_uses_full_speed_failsafe(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "fan.json")
            path.write_text('{"start_temp": "broken"}', encoding="utf-8")
            with mock.patch.object(fan_control, "CONFIG_FILE", str(path)):
                config = fan_control.initial_config()
        self.assertEqual(config["min_duty"], 100)
        self.assertEqual(config["gpio"], fan_control.ACTIVE_GPIO)
        self.assertEqual(fan_control.requested_duty(-100, config), 100)


if __name__ == "__main__":
    unittest.main()
