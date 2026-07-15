import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


class DashboardApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        psutil = types.ModuleType("psutil")
        psutil.cpu_percent = lambda interval=0: 0
        psutil.virtual_memory = lambda: types.SimpleNamespace(percent=0)
        sys.modules.setdefault("psutil", psutil)
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        os.environ["PIFAN_BASE"] = str(base)
        os.environ["PIFAN_PI_LIST"] = str(base / "pi_list.json")
        os.environ["PIFAN_CONFIG"] = str(base / "fan.json")
        os.environ["PIFAN_STATUS"] = str(base / "status.json")
        (base / "pi_list.json").write_text("[]", encoding="utf-8")
        (base / "fan.json").write_text(
            json.dumps({"gpio": 14, "start_temp": 45, "full_temp": 75, "min_duty": 45}),
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location(
            "dashboard", Path(__file__).parents[1] / "app" / "dashboard.py"
        )
        cls.dashboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.dashboard)
        cls.client = cls.dashboard.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_reads_fan_config(self):
        response = self.client.get("/fan-config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["start_temp"], 45)

    def test_updates_valid_fan_curve(self):
        response = self.client.put(
            "/fan-config",
            json={"gpio": 14, "start_temp": 50, "full_temp": 70, "min_duty": 40},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["full_temp"], 70)

    def test_rejects_curve_with_no_ramp(self):
        response = self.client.put(
            "/fan-config",
            json={"gpio": 14, "start_temp": 60, "full_temp": 62, "min_duty": 40},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_public_configuration_write(self):
        response = self.client.put(
            "/fan-config",
            json={"gpio": 14, "start_temp": 45, "full_temp": 75, "min_duty": 45},
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
