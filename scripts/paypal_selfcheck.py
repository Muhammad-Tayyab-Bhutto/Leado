"""One-command PayPal sandbox check for every API Dealwright uses.

    python scripts/paypal_selfcheck.py

Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from .env. Creates (and where
possible cleans up) sandbox objects only: refuses to run with PAYPAL_ENV=live.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
import paypal  # noqa: E402

if paypal.env() != "sandbox":
    raise SystemExit("Refusing to run against PAYPAL_ENV=live.")
if not paypal.configured():
    raise SystemExit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env first.")

results = []

def step(name, fn):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
        print(f"  PASS  {name}  {detail or ''}")
        return detail
    except Exception as e:
        results.append((False, name, str(e)))
        print(f"  FAIL  {name}\n        {e}")
        if os.environ.get("DEBUG"): traceback.print_exc()
        return None

print(f"PayPal {paypal.env()} self-check ({paypal.base()})\n")
step("OAuth token", lambda: "ok" if paypal._token() else None)

order = {}
def mk_order():
    o = paypal.api("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": paypal.currency(), "value": "1.00"}, "description": "Dealwright self-check"}],
        "payment_source": {"paypal": {"experience_context": {"return_url": "https://example.com/r", "cancel_url": "https://example.com/c",
                                                             "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}}}})
    order.update(o)
    link = paypal._link(o, "payer-action", "approve")
    assert link, "no approval link returned"
    return f"{o['id']} -> approval link ok"
step("Orders v2: create order", mk_order)
if order: step("Orders v2: get order", lambda: paypal.api("GET", f"/v2/checkout/orders/{order['id']}")["status"])

plan = {}
def mk_plan():
    prod = paypal.api("POST", "/v1/catalogs/products", {"name": "Dealwright self-check", "type": "SERVICE", "category": "SOFTWARE"})
    p = paypal.api("POST", "/v1/billing/plans", {
        "product_id": prod["id"], "name": "Self-check monthly",
        "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 1,
                            "total_cycles": 0, "pricing_scheme": {"fixed_price": {"value": "1.00", "currency_code": paypal.currency()}}}],
        "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 3}})
    plan.update(p)
    return p["id"]
step("Subscriptions v1: product + plan", mk_plan)
if plan:
    def mk_sub():
        s = paypal.api("POST", "/v1/billing/subscriptions", {"plan_id": plan["id"], "application_context": {
            "return_url": "https://example.com/r", "cancel_url": "https://example.com/c", "user_action": "SUBSCRIBE_NOW",
            "shipping_preference": "NO_SHIPPING"}})
        assert paypal._link(s, "approve"), "no approve link"
        return f"{s['id']} ({s.get('status')})"
    step("Subscriptions v1: create subscription", mk_sub)
    step("Subscriptions v1: deactivate test plan", lambda: paypal.api("POST", f"/v1/billing/plans/{plan['id']}/deactivate") or "ok")

def mk_invoice():
    inv = paypal.api("POST", "/v2/invoicing/invoices", {
        "detail": {"currency_code": paypal.currency(), "note": "Dealwright self-check"},
        "invoicer": {"business_name": paypal.brand()},
        "primary_recipients": [{"billing_info": {"email_address": "selfcheck@example.com"}}],
        "items": [{"name": "Self-check", "quantity": "1", "unit_amount": {"currency_code": paypal.currency(), "value": "1.00"}}]})
    iid = inv.get("id") or inv.get("href", "").rsplit("/", 1)[-1]
    assert iid, "no invoice id"
    paypal.api("DELETE", f"/v2/invoicing/invoices/{iid}")  # drafts can be deleted
    return f"{iid} created + deleted (draft)"
step("Invoicing v2: create + delete draft", mk_invoice)

if os.environ.get("PAYPAL_WEBHOOK_ID"):
    step("Webhooks: webhook id exists", lambda: paypal.api("GET", f"/v1/notifications/webhooks/{os.environ['PAYPAL_WEBHOOK_ID']}")["url"])
else:
    print("  SKIP  Webhooks (PAYPAL_WEBHOOK_ID not set; fine for local demos)")

bad = [r for r in results if not r[0]]
print(f"\n{len(results) - len(bad)}/{len(results)} checks passed.")
sys.exit(1 if bad else 0)
