import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.server import create_server
from backend.service import BankService


class LanAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bank = BankService(str(Path(self.tmp.name) / "bank.sqlite"))
        self.token = "trp_abcdefghijklmnopqrstuvwxyz123456"
        self.server = create_server("127.0.0.1", 0, service=self.bank,
                                    access_token=self.token, allowed_hosts=("10.219.251.94",))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", "/api/health", headers=headers or {})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_every_lan_api_request_requires_separate_token(self):
        lan_headers = {"Host": f"10.219.251.94:{self.server.server_port}",
                       "Origin": f"http://10.219.251.94:{self.server.server_port}"}
        status, body = self.request(lan_headers)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "LAN_ACCESS_REQUIRED")
        status, body = self.request(dict(lan_headers, **{"X-Tropos-Access": self.token}))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_loopback_remains_available_without_lan_token(self):
        status, body = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_allowed_private_host_header_can_reach_same_origin_api(self):
        status, body = self.request({"Host": f"10.219.251.94:{self.server.server_port}",
                                     "Origin": f"http://10.219.251.94:{self.server.server_port}",
                                     "X-Tropos-Access": self.token})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_non_loopback_binding_without_token_stays_forbidden(self):
        with self.assertRaisesRegex(ValueError, "explicit LAN access mode"):
            create_server("0.0.0.0", 0, service=self.bank)
        with self.assertRaisesRegex(ValueError, "requires at least one"):
            create_server("0.0.0.0", 0, service=self.bank, access_token=self.token)

    def test_explicit_open_lan_mode_needs_no_token(self):
        server = create_server("0.0.0.0", 0, service=self.bank,
                               allowed_hosts=("10.219.251.94",),
                               allow_unauthenticated_lan=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/api/health", headers={
                "Host": f"10.219.251.94:{server.server_port}",
                "Origin": f"http://10.219.251.94:{server.server_port}",
            })
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(body["ok"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
