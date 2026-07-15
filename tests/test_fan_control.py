import importlib.util
import sys
import types
import unittest
from pathlib import Path


gpio = types.ModuleType("RPi.GPIO")
rpi = types.ModuleType("RPi")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

spec = importlib.util.spec_from_file_location(
    "fan_control", Path(__file__).parents[1] / "app" / "fan_control.py"
)
fan_control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fan_control)


class FanCurveTests(unittest.TestCase):
    def setUp(self):
        self.config = {"start_temp": 45, "full_temp": 75, "min_duty": 45}

    def test_fan_is_off_below_start_temperature(self):
        self.assertEqual(fan_control.requested_duty(44.9, self.config), 0)

    def test_fan_starts_at_minimum_duty(self):
        self.assertEqual(fan_control.requested_duty(45, self.config), 45)

    def test_fan_reaches_full_duty(self):
        self.assertEqual(fan_control.requested_duty(75, self.config), 100)

    def test_fan_curve_is_linear(self):
        self.assertEqual(fan_control.requested_duty(60, self.config), 72)


if __name__ == "__main__":
    unittest.main()
