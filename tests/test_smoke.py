"""Smoke tests for Leado 2.0.

These exercise the HTTP layer, token auth, the public UI shell, and the
Blast Radius Zero containment proof. No external network or LLM keys needed.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Configure the app before importing crm (it reads the token at import time).
TEST_TOKEN = "ci-test-token-0123456789abcdef"
os.environ["SOFTORASYSTEMS_TOKEN"] = TEST_TOKEN
os.environ["SOFTORA_HOST"] = "127.0.0.1"
os.environ.setdefault("SANDBOX_DISABLE_DOCKER", "1")

import app  # noqa: E402
import crm  # noqa: E402
import sandbox  # noqa: E402


class LeadoSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(cls.tmp.name, "leads.db")
        crm.migrate()
        cls.server = crm.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read()

    def test_ui_is_public_and_contains_no_token(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Leado", body)
        self.assertNotIn(TEST_TOKEN.encode(), body)
        # The token input must never be prefilled with a credential.
        self.assertNotRegex(body.decode(), r'id="tok"[^>]*value="[^"]+"')

    def test_api_rejects_missing_token(self):
        status, _ = self.request("/api/stats")
        self.assertEqual(status, 401)

    def test_api_accepts_valid_token(self):
        status, body = self.request("/api/stats", crm.TOKEN)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("discovered", payload)

    def test_containment_proof_passes(self):
        result = sandbox.sandbox.verify_containment_proof()
        self.assertEqual(result["status"], "PASSED", result)
        self.assertFalse(result["details"]["read_host_etc"])
        self.assertFalse(result["details"]["access_env_secrets"])


if __name__ == "__main__":
    unittest.main()
