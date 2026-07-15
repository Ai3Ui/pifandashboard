import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from app.auth import SessionStore, create_auth_file, load_password_record, password_record, verify_password
from app.config import DEFAULT_CONFIG, atomic_write_json, normalize_fan_config, read_fan_config
from app.state import MAX_PIS, normalize_pi, normalize_pi_list, read_pi_list, update_self


class ConfigTests(unittest.TestCase):
    def test_valid_curve_is_canonical(self):
        value = normalize_fan_config(
            {"gpio": "14", "start_temp": "45", "full_temp": 75, "min_duty": "45", "hysteresis": 2}
        )
        self.assertEqual(value, DEFAULT_CONFIG)

    def test_rejects_nonfinite_fractional_and_unsafe_values(self):
        base = DEFAULT_CONFIG.copy()
        for change in (
            {"start_temp": float("nan")},
            {"full_temp": float("inf")},
            {"min_duty": 45.5},
            {"gpio": 17},
            {"start_temp": 71, "full_temp": 75},
        ):
            self.assertIsNone(normalize_fan_config({**base, **change}))

    def test_atomic_write_and_patch_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "fan.json")
            atomic_write_json(path, DEFAULT_CONFIG)
            subprocess.run(
                [sys.executable, "-m", "app.config", str(path), "--patch", "--gpio", "18"],
                check=True,
                cwd=Path(__file__).parents[1],
            )
            result = read_fan_config(path)
        self.assertEqual(result["gpio"], 18)
        self.assertEqual(result["start_temp"], DEFAULT_CONFIG["start_temp"])

    def test_canonicalize_adds_hysteresis_to_legacy_curve(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "fan.json")
            path.write_text(
                json.dumps({"gpio": 14, "start_temp": 35, "full_temp": 75, "min_duty": 65}),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "app.config", str(path), "--canonicalize"],
                check=True,
                cwd=Path(__file__).parents[1],
            )
            stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["hysteresis"], DEFAULT_CONFIG["hysteresis"])


class StateTests(unittest.TestCase):
    def test_accepts_only_rfc1918_or_loopback_ipv4(self):
        self.assertIsNotNone(normalize_pi({"name": "Pi", "ip": "192.168.1.2", "port": "8088"}))
        self.assertIsNone(normalize_pi({"name": "Pi", "ip": 2130706433, "port": 8088}))
        for address in ("8.8.8.8", "100.64.0.1", "169.254.1.1", "::1"):
            self.assertIsNone(normalize_pi({"name": "Pi", "ip": address, "port": 8088}))

    def test_list_rejects_duplicates_and_excess_entries(self):
        with self.assertRaises(ValueError):
            normalize_pi_list(
                [
                    {"name": "Pi", "ip": "10.0.0.1", "port": 8088},
                    {"name": "pi", "ip": "10.0.0.2", "port": 8088},
                ]
            )
        with self.assertRaises(ValueError):
            normalize_pi_list(
                [{"name": f"Pi {index}", "ip": f"10.0.0.{index + 1}", "port": 8088} for index in range(MAX_PIS + 1)]
            )

    def test_update_self_never_discards_corrupt_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "pis.json")
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                update_self(path, "192.168.1.2", 8088, "Pi")
            self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_update_self_is_atomic_and_canonical(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "pis.json")
            path.write_text("[]", encoding="utf-8")
            update_self(path, "192.168.1.2", "8088", "Pi")
            self.assertEqual(read_pi_list(path)[0]["ip"], "192.168.1.2")


class AuthTests(unittest.TestCase):
    def test_password_verification_handles_malformed_records(self):
        record = password_record("long enough password")
        self.assertTrue(verify_password("long enough password", record))
        self.assertFalse(verify_password("wrong password", record))
        self.assertFalse(verify_password("long enough password", []))

    def test_reset_invalidates_existing_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "auth.json")
            first_password = create_auth_file(path)
            sessions = SessionStore(path, ttl=30)
            token = sessions.login(first_password)
            self.assertTrue(sessions.valid(token))
            time.sleep(0.002)
            second_password = create_auth_file(path, force=True)
            self.assertFalse(sessions.valid(token))
            self.assertIsNone(sessions.login(first_password))
            self.assertIsNotNone(sessions.login(second_password))

    def test_auth_file_validation_rejects_corrupt_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "auth.json")
            password = create_auth_file(path)
            record, _mtime = load_password_record(path)
            self.assertTrue(verify_password(password, record))
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "authentication record"):
                load_password_record(path)

    def test_refuses_symlink_auth_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary, "target")
            link = Path(temporary, "auth.json")
            target.write_text("keep", encoding="utf-8")
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(RuntimeError):
                create_auth_file(link, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
