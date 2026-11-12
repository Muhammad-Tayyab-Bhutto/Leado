"""End-to-end tests for the PayPal quote-to-cash flow against a fake PayPal REST API.

Covers: pay link -> checkout redirect -> capture on return -> deal WON,
subscriptions, invoicing, refunds, webhook signature enforcement, and Wren routing.
No network or credentials needed.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SOFTORASYSTEMS_TOKEN", "ci-test-token-0123456789abcdef")
os.environ["SOFTORA_HOST"] = "127.0.0.1"
os.environ.setdefault("SANDBOX_DISABLE_DOCKER", "1")
# Empty strings (not pops) so load_dotenv() can't pull real keys from a local .env.
for k in ("VULTR_INFERENCE_API_KEY", "VULTR_API_KEY", "OPENROUTER_API_KEY", "PAYPAL_WEBHOOK_ID", "PUBLIC_BASE_URL"):
    os.environ[k] = ""

import app  # noqa: E402
import crm  # noqa: E402
import paypal  # noqa: E402
import copilot  # noqa: E402


class FakePayPal:
    """Minimal in-memory stand-in for the PayPal REST endpoints Dealwright calls."""
    def __init__(self):
        self.calls, self.n = [], 0

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n:04d}"

    def __call__(self, method, path, body=None, idempotency_key=None):
        self.calls.append((method, path, body))
        if path == "/v2/checkout/orders":
            oid = self._id("ORD")
            return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
        if path.endswith("/capture"):
            return {"status": "COMPLETED", "payer": {"email_address": "buyer@example.com"},
                    "purchase_units": [{"payments": {"captures": [{"id": "CAP" + path.split("/")[3], "status": "COMPLETED"}]}}]}
        if path == "/v1/catalogs/products": return {"id": self._id("PROD")}
        if path == "/v1/billing/plans": return {"id": self._id("P-")}
        if path == "/v1/billing/subscriptions":
            sid = self._id("I-")
            return {"id": sid, "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token={sid}"}]}
        if path.startswith("/v1/billing/subscriptions/") and method == "GET":
            return {"status": "ACTIVE", "subscriber": {"email_address": "buyer@example.com"}}
        if path.endswith("/cancel"): return {}
        if path == "/v2/invoicing/invoices": return {"id": self._id("INV2-")}
        if path.endswith("/send"): return {"href": "https://www.sandbox.paypal.com/invoice/p/#INV"}
        if path.endswith("/refund"): return {"id": self._id("REF"), "status": "COMPLETED"}
        raise AssertionError(f"unexpected PayPal call {method} {path}")


class PayPalFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(cls.tmp.name, "leads.db")
        os.environ["PAYPAL_CLIENT_ID"] = "test-id"
        os.environ["PAYPAL_CLIENT_SECRET"] = "test-secret"
        crm.migrate()
        cls.fake = FakePayPal()
        cls._orig_api = paypal.api
        paypal.api = cls.fake
        cls.server = crm.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        paypal.api = cls._orig_api
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()
        os.environ.pop("PAYPAL_CLIENT_ID", None); os.environ.pop("PAYPAL_CLIENT_SECRET", None)

    # -- helpers ---------------------------------------------------------
    def http(self, method, path, body=None, auth=True, form=None):
        data = json.dumps(body).encode() if body is not None else (urllib.parse.urlencode(form).encode() if form else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if auth: req.add_header("Authorization", "Bearer " + crm.TOKEN)
        req.add_header("Content-Type", "application/json" if body is not None else "application/x-www-form-urlencoded")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read(), dict(ex.headers)

    def make_lead(self, domain, email="cto@" + "example.com"):
        with app.conn() as c:
            c.execute("insert into leads(domain, company, contact_email, contact_name, score, est_mrr_low, est_mrr_high) values(?,?,?,?,?,?,?)",
                      (domain, domain.split(".")[0].title(), email, "Ada Lovelace", 80, 1500, 4000))
            return c.execute("select id from leads where domain=?", (domain,)).fetchone()[0]

    def proposal_token(self, lead_id):
        s, b, _ = self.http("POST", f"/api/leads/{lead_id}/proposal", {})
        self.assertEqual(s, 200, b)
        pid = json.loads(b)["id"]
        s, b, _ = self.http("POST", f"/api/proposals/{pid}/paylink", {})
        self.assertEqual(s, 200, b)
        url = json.loads(b)["url"]
        self.assertIn("/pay/", url)
        return url.rsplit("/", 1)[-1]

    def lead(self, lead_id):
        with app.conn() as c: return dict(c.execute("select * from leads where id=?", (lead_id,)).fetchone())

    # -- tests -----------------------------------------------------------
    def test_one_time_checkout_captures_and_wins_deal(self):
        lid = self.make_lead("acme.io")
        tok = self.proposal_token(lid)
        s, body, _ = self.http("GET", f"/pay/{tok}", auth=False)
        self.assertEqual(s, 200); self.assertIn(b"Pay with PayPal", body)
        self.assertNotIn(b"Lead Score", body)  # internal sales data never reaches the client
        s, _, h = self.http("POST", f"/pay/{tok}/checkout", auth=False, form={"tier": "0"})
        self.assertEqual(s, 303); self.assertIn("sandbox.paypal.com", h["Location"])
        oid = urllib.parse.parse_qs(urllib.parse.urlparse(h["Location"]).query)["token"][0]
        s, body, _ = self.http("GET", f"/pay/{tok}/return?token={oid}", auth=False)
        self.assertEqual(s, 200, body); self.assertIn(b"Payment received", body)
        l = self.lead(lid)
        self.assertEqual(l["status"], "WON"); self.assertEqual(l["est_project_value"], 3000)
        # second return is idempotent: no second capture
        captures = [c for c in self.fake.calls if c[1].endswith("/capture")]
        self.http("GET", f"/pay/{tok}/return?token={oid}", auth=False)
        self.assertEqual(len(captures), len([c for c in self.fake.calls if c[1].endswith("/capture")]))
        # a paid proposal cannot be checked out again
        s, body, _ = self.http("POST", f"/pay/{tok}/checkout", auth=False, form={"tier": "0"})
        self.assertNotEqual(s, 303); self.assertIn(b"already paid", body)
        # refund
        pay = next(p for p in paypal.list_payments() if p["paypal_id"] == oid)
        s, b, _ = self.http("POST", f"/api/payments/{pay['id']}/refund", {"confirm": True})
        self.assertEqual(s, 200, b); self.assertEqual(json.loads(b)["status"], "refunded")

    def test_monthly_tier_creates_subscription_and_sets_mrr(self):
        lid = self.make_lead("globex.com")
        tok = self.proposal_token(lid)
        s, _, h = self.http("POST", f"/pay/{tok}/checkout", auth=False, form={"tier": "1"})
        self.assertEqual(s, 303)
        sid = next(p["paypal_id"] for p in paypal.list_payments() if p["kind"] == "subscription" and p["lead_id"] == lid)
        s, body, _ = self.http("GET", f"/pay/{tok}/return?subscription_id={sid}&ba_token=x&token=y", auth=False)
        self.assertEqual(s, 200, body); self.assertIn(b"subscription is active", body)
        self.assertEqual(self.lead(lid)["actual_mrr"], 4000)

    def test_invoice_requires_confirmation_then_sends(self):
        lid = self.make_lead("initech.dev")
        s, _, _ = self.http("POST", f"/api/leads/{lid}/invoice", {"tier": 0})
        self.assertEqual(s, 400)
        s, b, _ = self.http("POST", f"/api/leads/{lid}/invoice", {"tier": 0, "confirm": True})
        self.assertEqual(s, 200, b)
        inv = json.loads(b)
        self.assertEqual((inv["kind"], inv["status"]), ("invoice", "sent"))
        # AI collections agent (template fallback without an LLM key) queues a draft for approval
        s, b, _ = self.http("POST", f"/api/payments/{inv['id']}/remind", {})
        self.assertEqual(s, 200, b)
        with app.conn() as c:
            e = c.execute("select status, approach from emails where id=?", (json.loads(b)["email_id"],)).fetchone()
        self.assertEqual(tuple(e), ("draft", "PAYPAL_REMINDER"))

    def test_invalid_pay_token_and_unverified_webhook_rejected(self):
        s, _, _ = self.http("GET", "/pay/not-a-real-token-123456", auth=False)
        self.assertEqual(s, 404)
        s, b, _ = self.http("POST", "/api/paypal/webhook", {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}, auth=False)
        self.assertEqual(s, 400, b)

    def test_checkout_is_throttled_per_link(self):
        tok = self.proposal_token(self.make_lead("umbrella.co"))
        codes = [self.http("POST", f"/pay/{tok}/checkout", auth=False, form={"tier": "0"})[0] for _ in range(paypal.CHECKOUT_LIMIT + 1)]
        self.assertEqual(codes[:-1], [303] * paypal.CHECKOUT_LIMIT)
        self.assertNotEqual(codes[-1], 303)

    def test_payment_apis_require_token(self):
        s, _, _ = self.http("GET", "/api/payments", auth=False)
        self.assertEqual(s, 401)

    def test_vio_routes_money_actions_to_confirmations(self):
        lid = self.make_lead("hooli.ai")
        r = copilot.vio.handle_query("invoice hooli for the monthly growth plan")
        act = r["actions"][0]
        self.assertEqual(act["type"], "api"); self.assertTrue(act["body"]["confirm"]); self.assertIn("confirm", act)
        self.assertEqual(act["path"], f"/api/leads/{lid}/invoice"); self.assertEqual(act["body"]["tier"], 1)
        r = copilot.vio.handle_query("who still owes us money?")
        self.assertIn("PayPal cash position", r["reply"])


if __name__ == "__main__":
    unittest.main()
