import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import flask  # noqa: F401
except ImportError:  # The full suite runs on the target Pi, where Flask is a package dependency.
    flask = None

from app.auth import password_record


@unittest.skipIf(flask is None, "Flask is not installed in this development interpreter")
class DashboardApiTests(unittest.TestCase):
    PASSWORD = "correct horse battery staple"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        web = cls.base / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
        cls.paths = {
            "PIFAN_BASE": str(cls.base),
            "PIFAN_PI_LIST": str(cls.base / "pi_list.json"),
            "PIFAN_CONFIG": str(cls.base / "fan.json"),
            "PIFAN_STATUS": str(cls.base / "status.json"),
            "PIFAN_AUTH": str(cls.base / "auth.json"),
        }
        os.environ.update(cls.paths)
        (cls.base / "auth.json").write_text(json.dumps(password_record(cls.PASSWORD)), encoding="utf-8")
        sys.modules.pop("app.dashboard", None)
        cls.dashboard = importlib.import_module("app.dashboard")
        cls.dashboard.metrics = mock.Mock(
            snapshot=mock.Mock(
                return_value={"cpu": 12.5, "memory": 34.5, "disk": 56.5, "uptime": 120, "load": 0.25}
            )
        )

    @classmethod
    def tearDownClass(cls):
        cls.dashboard.fleet_executor.shutdown(wait=False, cancel_futures=True)
        cls.temporary.cleanup()

    def setUp(self):
        Path(self.paths["PIFAN_PI_LIST"]).write_text("[]\n", encoding="utf-8")
        Path(self.paths["PIFAN_CONFIG"]).write_text(
            json.dumps(
                {"gpio": 14, "start_temp": 45, "full_temp": 75, "min_duty": 45, "hysteresis": 2}
            ),
            encoding="utf-8",
        )
        Path(self.paths["PIFAN_STATUS"]).write_text(
            json.dumps(
                {
                    "temperature": 42.5,
                    "speed": 0,
                    "gpio": 14,
                    "curve": {"start_temp": 45, "full_temp": 75, "min_duty": 45, "hysteresis": 2},
                    "updated_at": time.time(),
                    "error": None,
                }
            ),
            encoding="utf-8",
        )
        self.dashboard.limiter.requests.clear()
        self.dashboard.sessions._sessions.clear()
        self.dashboard.fleet_cache.update(key=None, updated=0.0, results=[])
        self.client = self.dashboard.app.test_client()

    def token(self):
        response = self.client.post("/auth/login", json={"password": self.PASSWORD})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["token"]

    def auth(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def test_status_is_private_and_has_security_headers(self):
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["temperature"], 42.5)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        public = self.client.get("/status", environ_base={"REMOTE_ADDR": "8.8.8.8"})
        self.assertEqual(public.status_code, 403)

    def test_management_requires_authentication(self):
        self.assertEqual(self.client.get("/fan-config").status_code, 401)
        self.assertEqual(self.client.get("/fleet-status").status_code, 401)

    def test_rate_limiter_enforces_its_window(self):
        limiter = self.dashboard.RateLimiter()
        self.assertTrue(limiter.allow(("client", "test"), 2, 60))
        self.assertTrue(limiter.allow(("client", "test"), 2, 60))
        self.assertFalse(limiter.allow(("client", "test"), 2, 60))

    def test_login_rejects_wrong_password_and_accepts_correct_password(self):
        self.assertEqual(self.client.post("/auth/login", json={"password": "incorrect!"}).status_code, 401)
        token = self.token()
        checked = self.client.get("/auth/check", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(checked.status_code, 200)

    def test_configuration_write_and_gpio_lock(self):
        headers = self.auth()
        valid = self.client.put(
            "/fan-config",
            headers=headers,
            json={"gpio": 14, "start_temp": 48, "full_temp": 72, "min_duty": 50, "hysteresis": 3},
        )
        self.assertEqual(valid.status_code, 200)
        changed_gpio = self.client.put(
            "/fan-config",
            headers=headers,
            json={"gpio": 18, "start_temp": 48, "full_temp": 72, "min_duty": 50, "hysteresis": 3},
        )
        self.assertEqual(changed_gpio.status_code, 409)
        invalid = self.client.put(
            "/fan-config",
            headers=headers,
            json={"gpio": 14, "start_temp": 70, "full_temp": 72, "min_duty": 50, "hysteresis": 3},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_pi_crud_validates_private_addresses_and_duplicates(self):
        headers = self.auth()
        added = self.client.post(
            "/add_pi", headers=headers, json={"name": "Workshop", "ip": "192.168.50.40", "port": 8088}
        )
        self.assertEqual(added.status_code, 201)
        duplicate = self.client.post(
            "/add_pi", headers=headers, json={"name": "Other", "ip": "192.168.50.40", "port": 8089}
        )
        self.assertEqual(duplicate.status_code, 409)
        duplicate_name = self.client.post(
            "/add_pi", headers=headers, json={"name": "workshop", "ip": "192.168.50.41", "port": 8089}
        )
        self.assertEqual(duplicate_name.status_code, 409)
        public = self.client.post(
            "/add_pi", headers=headers, json={"name": "Bad", "ip": "8.8.8.8", "port": 8088}
        )
        self.assertEqual(public.status_code, 400)
        second = self.client.post(
            "/add_pi", headers=headers, json={"name": "Second", "ip": "192.168.50.41", "port": 8089}
        )
        self.assertEqual(second.status_code, 201)
        duplicate_edit = self.client.post(
            "/edit_pi",
            headers=headers,
            json={
                "originalIp": "192.168.50.41",
                "name": "WORKSHOP",
                "ip": "192.168.50.41",
                "port": 8089,
            },
        )
        self.assertEqual(duplicate_edit.status_code, 409)
        deleted = self.client.post("/delete_pi", headers=headers, json={"ip": "192.168.50.40"})
        self.assertEqual(deleted.status_code, 200)

    def test_oversized_json_is_rejected(self):
        response = self.client.post(
            "/auth/login", data=b"{" + b"x" * (17 * 1024) + b"}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 413)

    def test_fleet_uses_backend_fetch_and_remote_curve(self):
        Path(self.paths["PIFAN_PI_LIST"]).write_text(
            json.dumps([{"name": "Peer", "ip": "192.168.50.40", "port": 8088}]), encoding="utf-8"
        )
        peer_data = {
            "name": "Peer",
            "ip": "192.168.50.40",
            "port": 8088,
            "data": {
                "temperature": 40,
                "speed": 0,
                "curve": {"start_temp": 40, "full_temp": 70, "min_duty": 40, "hysteresis": 2},
            },
        }
        with mock.patch.object(self.dashboard, "fetch_pi_status", return_value=peer_data):
            response = self.client.get("/fleet-status", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[0]["data"]["curve"]["start_temp"], 40)

    def serve_once(self, payload, drip=False):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def worker():
            try:
                connection, _address = server.accept()
                with connection:
                    connection.recv(2048)
                    if drip:
                        for byte in payload:
                            try:
                                connection.send(bytes([byte]))
                            except OSError:
                                break
                            time.sleep(0.25)
                    else:
                        connection.sendall(payload)
            finally:
                server.close()

        threading.Thread(target=worker, daemon=True).start()
        return port

    def test_peer_redirect_is_not_followed(self):
        payload = b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\nContent-Length: 0\r\n\r\n"
        port = self.serve_once(payload)
        result = self.dashboard.fetch_pi_status({"name": "Peer", "ip": "127.0.0.1", "port": port})
        self.assertIn("error", result["data"])

    def test_valid_peer_response_is_sanitised(self):
        status = {
            "temperature": 41.25,
            "speed": 52,
            "cpu": 10,
            "memory": 20,
            "disk": 30,
            "load": 0.5,
            "uptime": 123,
            "hostname": "Peer",
            "gpio": 14,
            "curve": {"start_temp": 40, "full_temp": 70, "min_duty": 40, "hysteresis": 2},
            "controller_error": None,
        }
        body = json.dumps(status).encode()
        payload = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        port = self.serve_once(payload)
        result = self.dashboard.fetch_pi_status({"name": "Peer", "ip": "127.0.0.1", "port": port})
        self.assertEqual(result["data"]["temperature"], 41.2)
        self.assertEqual(result["data"]["curve"]["start_temp"], 40.0)

    def test_slow_peer_has_hard_total_deadline(self):
        port = self.serve_once(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}", drip=True)
        started = time.monotonic()
        result = self.dashboard.fetch_pi_status({"name": "Peer", "ip": "127.0.0.1", "port": port})
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertIn("error", result["data"])

    def test_malformed_peer_data_is_isolated(self):
        for exception in (OverflowError("number is too large"), RecursionError("too deeply nested")):
            body = b"{}"
            payload = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            port = self.serve_once(payload)
            with mock.patch.object(self.dashboard, "clean_remote_status", side_effect=exception):
                result = self.dashboard.fetch_pi_status({"name": "Peer", "ip": "127.0.0.1", "port": port})
            self.assertIn("error", result["data"])


if __name__ == "__main__":
    unittest.main()
