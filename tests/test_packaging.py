import os
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class PackagingTests(unittest.TestCase):
    def test_legacy_remote_installers_are_removed(self):
        self.assertFalse((ROOT / "fanscripts" / "rpi" / "rpi_install_fan_control.sh").exists())
        self.assertFalse((ROOT / "webinterface" / "script" / "rpi" / "rpi_install_pi_manager.sh").exists())

    def test_installer_has_port_and_state_safety_guards(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        for expected in (
            "select_auto_port",
            "8088-8188",
            "port_is_listening",
            "check_mutable_state_files",
            'STATE_PYTHONPATH="$STAGE_DIR"',
            "app.state",
            "pifandashboard.previous",
        ):
            self.assertIn(expected, installer)
        self.assertNotIn("rm -rf /var/www", installer)
        self.assertNotIn("curl |", installer)

    @unittest.skipIf(os.name == "nt", "Git executable bits are not represented by Windows stat")
    def test_entry_points_are_executable(self):
        for name in ("install.sh", "uninstall.sh", "fandashboardsetup.sh", "fandashboarduninstaller.sh"):
            self.assertTrue(os.access(ROOT / name, os.X_OK), name)


if __name__ == "__main__":
    unittest.main()
