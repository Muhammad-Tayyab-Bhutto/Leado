"""PayPal quote-to-cash layer for Dealwright.

Turns an AI-generated proposal into money:

* **Orders v2**          one-time tiers  -> hosted PayPal checkout -> capture
* **Subscriptions v1**   monthly tiers   -> product + plan + subscription
* **Invoicing v2**       Wren "invoice <company>" -> draft + send PayPal invoice
* **Refunds**            Wren "refund <company>"  -> refund the captured payment
* **Webhooks**           signature-verified events keep the CRM in sync

All calls go to the PayPal sandbox unless PAYPAL_ENV=live. Credentials are read
from PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET. Money-moving actions are only ever
executed from an explicit human click; the AI proposes, a person confirms.
"""
import json, os, re, secrets, threading, time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

import app
from app import conn, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INT NOT NULL,
    proposal_id INT,
    tier_index INT,
    tier_name TEXT,
    kind TEXT NOT NULL,              -- order | subscription | invoice
    paypal_id TEXT UNIQUE,           -- order id / subscription id / invoice id
    capture_id TEXT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    interval TEXT NOT NULL DEFAULT 'once',   -- once | month
    status TEXT NOT NULL DEFAULT 'created',  -- created|sent|completed|active|paid|refunded|cancelled|failed
    approve_url TEXT,
    payer_email TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS paypal_webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT,
    verified INT,
    received_at TEXT
);
"""
SETTLED = {"completed", "active", "paid"}
OPEN = {"created", "sent"}


# ---------------------------------------------------------------------------
# Config + low-level client
# ---------------------------------------------------------------------------

def env() -> str:
    return "live" if os.environ.get("PAYPAL_ENV", "sandbox").lower() == "live" else "sandbox"

def base() -> str:
    return "https://api-m.paypal.com" if env() == "live" else "https://api-m.sandbox.paypal.com"

def currency() -> str:
    return os.environ.get("PAYPAL_CURRENCY", "USD").upper()

def brand() -> str:
    return os.environ.get("PAYPAL_BRAND_NAME", "Dealwright")[:127]

def configured() -> bool:
    return bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))

def status() -> Dict[str, Any]:
    return {"configured": configured(), "env": env(), "currency": currency(),
            "webhook_verification": bool(os.environ.get("PAYPAL_WEBHOOK_ID")),
            "apis": ["Orders v2", "Subscriptions v1", "Invoicing v2", "Payments v2 (refunds)", "Webhooks"]}

_tok: Dict[str, Any] = {"value": None, "exp": 0.0}
_tok_lock = threading.Lock()

def _token() -> str:
    if not configured():
        raise NotImplementedError("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (developer.paypal.com > Apps & Credentials, Sandbox)")
    with _tok_lock:
        if _tok["value"] and time.time() < _tok["exp"] - 60:
            return _tok["value"]
        r = requests.post(base() + "/v1/oauth2/token", data={"grant_type": "client_credentials"},
                          auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"PayPal auth failed ({r.status_code}): check sandbox client id/secret")
        d = r.json()
        _tok.update(value=d["access_token"], exp=time.time() + int(d.get("expires_in", 3000)))
        return _tok["value"]

def api(method: str, path: str, body: Optional[dict] = None, idempotency_key: Optional[str] = None) -> dict:
    """Call the PayPal REST API. Raises RuntimeError with PayPal's own message on failure."""
    h = {"Authorization": "Bearer " + _token(), "Content-Type": "application/json", "Prefer": "return=representation"}
    if idempotency_key: h["PayPal-Request-Id"] = idempotency_key
    r = requests.request(method, base() + path, headers=h, data=json.dumps(body) if body is not None else None, timeout=45)
    if r.status_code >= 400:
        try:
            e = r.json(); msg = e.get("message") or e.get("error_description") or e.get("name")
            det = "; ".join(d.get("description") or d.get("issue", "") for d in e.get("details", []))
            msg = f"{msg} ({det})" if det else msg
        except Exception:
            msg = r.text[:300]
        raise RuntimeError(f"PayPal {method} {path.split('?')[0]} failed [{r.status_code}]: {msg}")
    return r.json() if r.content else {}

def _link(resp: dict, *rels: str) -> Optional[str]:
    for rel in rels:
        for l in resp.get("links", []):
            if l.get("rel") == rel: return l.get("href")
    return None


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def ensure():
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("pragma table_info(proposals)")}
        if cols and "pay_token" not in cols:
            c.execute("alter table proposals add column pay_token TEXT")

def _row(c, pid: int):
    r = c.execute("select * from payments where id=?", (pid,)).fetchone()
    if not r: raise LookupError("payment not found")
    return dict(r)

def _by_paypal_id(c, ppid: str):
    r = c.execute("select * from payments where paypal_id=?", (ppid,)).fetchone()
    return dict(r) if r else None

def _event(c, lead_id, kind, detail):
    c.execute("insert into events(lead_id,kind,detail,at) values(?,?,?,?)", (lead_id, kind, str(detail)[:500], now()))

def _proposal_tiers(p: dict) -> list:
    t = p.get("pricing_tiers")
    if isinstance(t, str):
        try: t = json.loads(t)
        except Exception: t = []
    return t or []

def tier_amount(tier: dict) -> Tuple[float, str]:
    """(amount, 'once'|'month') from a pricing tier. Uses numeric fields when present, else parses '$3,000/month'."""
    if tier.get("amount"):
        return round(float(tier["amount"]), 2), tier.get("interval", "once")
    price = str(tier.get("price", ""))
    m = re.search(r"([\d,]+(?:\.\d+)?)", price)
    if not m: raise ValueError(f"cannot read a price from tier '{tier.get('name')}'")
    return round(float(m.group(1).replace(",", "")), 2), ("month" if re.search(r"/\s*mo|month", price, re.I) else "once")

def _money(v: float) -> str:
    return f"{v:.2f}"


# ---------------------------------------------------------------------------
# Pay links (public, unguessable token per proposal)
# ---------------------------------------------------------------------------

def pay_link(proposal_id: int, base_url: str) -> Dict[str, Any]:
    ensure()
    with conn() as c:
        p = c.execute("select * from proposals where id=?", (proposal_id,)).fetchone()
        if not p: raise LookupError("proposal not found")
        tok = p["pay_token"] or secrets.token_urlsafe(24)
        if not p["pay_token"]:
            c.execute("update proposals set pay_token=?, status=case when status='draft' then 'sent' else status end, updated_at=? where id=?",
                      (tok, now(), proposal_id))
            _event(c, p["lead_id"], "paypal_link", "PayPal checkout link created for proposal")
    return {"url": f"{base_url.rstrip('/')}/pay/{tok}", "token": tok}

def proposal_for_token(tok: str) -> Tuple[dict, dict]:
    ensure()
    if not tok or len(tok) > 64: raise LookupError("invalid link")
    with conn() as c:
        p = c.execute("select * from proposals where pay_token=?", (tok,)).fetchone()
        if not p: raise LookupError("invalid link")
        l = c.execute("select * from leads where id=?", (p["lead_id"],)).fetchone()
    return dict(p), dict(l)


# ---------------------------------------------------------------------------
# Checkout: Orders (one-time) and Subscriptions (monthly)
# ---------------------------------------------------------------------------

_recent: Dict[str, list] = {}
_recent_lock = threading.Lock()
CHECKOUT_LIMIT = int(os.environ.get("PAYPAL_CHECKOUT_LIMIT", "10"))  # per pay link per 10 minutes

def _throttle(tok: str):
    t = time.time()
    with _recent_lock:
        hits = [x for x in _recent.get(tok, []) if t - x < 600]
        if len(hits) >= CHECKOUT_LIMIT:
            raise ValueError("Too many checkout attempts for this link; please wait a few minutes.")
        hits.append(t); _recent[tok] = hits

def start_checkout(tok: str, tier_index: int, base_url: str) -> str:
    """Create a PayPal order or subscription for the chosen tier and return the buyer approval URL."""
    p, l = proposal_for_token(tok)
    if p.get("status") == "paid": raise ValueError("This proposal is already paid.")
    _throttle(tok)
    tiers = _proposal_tiers(p)
    if not (0 <= tier_index < len(tiers)): raise ValueError("unknown pricing tier")
    tier = tiers[tier_index]
    amount, interval = tier_amount(tier)
    ret, cancel = f"{base_url}/pay/{tok}/return", f"{base_url}/pay/{tok}"
    desc = f"{tier.get('name', 'Service')} - {p.get('title', '')}"[:127]
    custom = f"Dealwright:{p['id']}:{tier_index}"

    if interval == "month":
        product = api("POST", "/v1/catalogs/products", {"name": f"{brand()} - {l.get('company') or 'Client'}"[:127],
                                                         "type": "SERVICE", "category": "SOFTWARE", "description": desc})
        plan = api("POST", "/v1/billing/plans", {
            "product_id": product["id"], "name": tier.get("name", "Monthly plan")[:127], "description": desc,
            "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR",
                                "sequence": 1, "total_cycles": 0,
                                "pricing_scheme": {"fixed_price": {"value": _money(amount), "currency_code": currency()}}}],
            "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 3}})
        sub = api("POST", "/v1/billing/subscriptions", {
            "plan_id": plan["id"], "custom_id": custom,
            "application_context": {"brand_name": brand(), "user_action": "SUBSCRIBE_NOW", "shipping_preference": "NO_SHIPPING",
                                    "return_url": ret, "cancel_url": cancel}})
        ppid, url, kind = sub["id"], _link(sub, "approve"), "subscription"
    else:
        order = api("POST", "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": str(p["id"]), "custom_id": custom, "description": desc,
                                "amount": {"currency_code": currency(), "value": _money(amount)}}],
            "payment_source": {"paypal": {"experience_context": {
                "brand_name": brand(), "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                "return_url": ret, "cancel_url": cancel}}}}, idempotency_key=secrets.token_hex(16))
        ppid, url, kind = order["id"], _link(order, "payer-action", "approve"), "order"

    if not url: raise RuntimeError("PayPal did not return an approval link")
    with conn() as c:
        c.execute("""insert into payments(lead_id,proposal_id,tier_index,tier_name,kind,paypal_id,amount,currency,interval,status,approve_url,created_at,updated_at)
                     values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (l["id"], p["id"], tier_index, tier.get("name"), kind, ppid, amount, currency(), interval, "created", url, now(), now()))
        _event(c, l["id"], "paypal_checkout", f"Client opened PayPal checkout: {tier.get('name')} ${amount:,.2f}{'/mo' if interval == 'month' else ''}")
    return url

def complete_return(tok: str, q: dict) -> dict:
    """Buyer came back from PayPal: capture the order / confirm the subscription. Safe to call twice."""
    p, _ = proposal_for_token(tok)
    ppid = (q.get("subscription_id") or q.get("token") or [""])[0]
    with conn() as c:
        pay = _by_paypal_id(c, ppid)
    if not pay or pay["proposal_id"] != p["id"]: raise LookupError("payment not found for this proposal")
    if pay["status"] in SETTLED: return pay
    if pay["kind"] == "order":
        res = api("POST", f"/v2/checkout/orders/{ppid}/capture", {}, idempotency_key=f"capture-{ppid}")
        cap = (res.get("purchase_units") or [{}])[0].get("payments", {}).get("captures", [{}])[0]
        if res.get("status") != "COMPLETED" and cap.get("status") != "COMPLETED":
            raise RuntimeError(f"PayPal capture not completed (status {res.get('status')})")
        payer = (res.get("payer") or {}).get("email_address") or (res.get("payment_source", {}).get("paypal", {}).get("email_address"))
        return settle(ppid, "completed", capture_id=cap.get("id"), payer_email=payer, source="return")
    sub = api("GET", f"/v1/billing/subscriptions/{ppid}")
    if sub.get("status") not in ("ACTIVE", "APPROVED"):
        raise RuntimeError(f"Subscription is {sub.get('status')}, not active yet")
    return settle(ppid, "active", payer_email=(sub.get("subscriber") or {}).get("email_address"), source="return")


# ---------------------------------------------------------------------------
# State transitions (shared by return URL, webhooks and manual sync)
# ---------------------------------------------------------------------------

def settle(ppid: str, new_status: str, capture_id: str = None, payer_email: str = None, source: str = "") -> dict:
    """Mark a payment settled and move the deal to WON. Idempotent."""
    with conn() as c:
        pay = _by_paypal_id(c, ppid)
        if not pay: raise LookupError("unknown PayPal id")
        if pay["status"] in SETTLED or pay["status"] == "refunded":
            return pay
        c.execute("update payments set status=?, capture_id=coalesce(?,capture_id), payer_email=coalesce(?,payer_email), updated_at=? where id=?",
                  (new_status, capture_id, payer_email, now(), pay["id"]))
        today = datetime.now(timezone.utc).date().isoformat()
        if pay["interval"] == "month":
            c.execute("update leads set actual_mrr=?, monthly_retainer=? where id=?", (int(pay["amount"]), int(pay["amount"]), pay["lead_id"]))
        else:
            c.execute("update leads set est_project_value=? where id=?", (int(pay["amount"]), pay["lead_id"]))
        c.execute("update leads set status='WON', client_status='active', acquired_at=coalesce(acquired_at,?), contract_start=coalesce(contract_start,?) where id=?",
                  (today, today, pay["lead_id"]))
        if pay["proposal_id"]:
            c.execute("update proposals set status='paid', updated_at=? where id=?", (now(), pay["proposal_id"]))
        what = {"order": "payment captured", "subscription": "subscription activated", "invoice": "invoice paid"}[pay["kind"]]
        _event(c, pay["lead_id"], "paypal_paid",
               f"PayPal {what}: {pay['tier_name']} ${pay['amount']:,.2f}{'/mo' if pay['interval'] == 'month' else ''} ({source}). Deal moved to WON.")
        return _row(c, pay["id"])

def _set_status(ppid: str, st: str, note: str):
    with conn() as c:
        pay = _by_paypal_id(c, ppid)
        if not pay or pay["status"] == st: return pay
        c.execute("update payments set status=?, updated_at=? where id=?", (st, now(), pay["id"]))
        if st in ("refunded", "cancelled") and pay["interval"] == "month":
            c.execute("update leads set actual_mrr=0, client_status='churned' where id=?", (pay["lead_id"],))
        _event(c, pay["lead_id"], f"paypal_{st}", note)
        return _row(c, pay["id"])


# ---------------------------------------------------------------------------
# Invoicing, refunds, sync (triggered by humans via UI or confirmed Wren actions)
# ---------------------------------------------------------------------------

def send_invoice(lead_id: int, tier_index: int = 0) -> dict:
    ensure()
    import proposal_engine
    with conn() as c:
        l = c.execute("select * from leads where id=?", (lead_id,)).fetchone()
        if not l: raise LookupError("lead not found")
        l = dict(l)
    if not l.get("contact_email"): raise ValueError(f"{l['company']} has no contact email; add one before invoicing")
    p = proposal_engine.get_proposal(lead_id) or proposal_engine.generate_proposal(lead_id)
    tiers = _proposal_tiers(p)
    if not (0 <= tier_index < len(tiers)): raise ValueError("unknown pricing tier")
    tier = tiers[tier_index]
    amount, interval = tier_amount(tier)
    inv = api("POST", "/v2/invoicing/invoices", {
        "detail": {"currency_code": currency(), "note": f"Thank you for choosing {brand()}. Scope: {p.get('title', '')}"[:4000],
                   "terms_and_conditions": "Payable within 10 days.", "payment_term": {"term_type": "NET_10"}},
        "invoicer": {"business_name": brand()},
        "primary_recipients": [{"billing_info": {"email_address": l["contact_email"],
                                                 **({"name": {"full_name": l["contact_name"]}} if l.get("contact_name") else {})}}],
        "items": [{"name": tier.get("name", "Services")[:200],
                   "description": ((tier.get("highlight") or "") + (" (first month)" if interval == "month" else ""))[:1000],
                   "quantity": "1", "unit_amount": {"currency_code": currency(), "value": _money(amount)}, "unit_of_measure": "AMOUNT"}]})
    inv_id = inv.get("id") or (inv.get("href", "").rsplit("/", 1)[-1])
    sent = api("POST", f"/v2/invoicing/invoices/{inv_id}/send", {"send_to_recipient": True, "send_to_invoicer": False})
    url = sent.get("href") or _link(sent, "payer-view")
    if not url:
        detail = api("GET", f"/v2/invoicing/invoices/{inv_id}")
        url = (detail.get("detail", {}).get("metadata") or {}).get("recipient_view_url")
    with conn() as c:
        c.execute("""insert into payments(lead_id,proposal_id,tier_index,tier_name,kind,paypal_id,amount,currency,interval,status,approve_url,created_at,updated_at)
                     values(?,?,?,?,'invoice',?,?,?,?,'sent',?,?,?)""",
                  (lead_id, p["id"], tier_index, tier.get("name"), inv_id, amount, currency(), interval, url, now(), now()))
        c.execute("update proposals set status=case when status in ('draft','sent') then 'invoiced' else status end, updated_at=? where id=?", (now(), p["id"]))
        _event(c, lead_id, "paypal_invoice", f"PayPal invoice {inv_id} sent to {l['contact_email']} for ${amount:,.2f}")
        pid = c.execute("select id from payments where paypal_id=?", (inv_id,)).fetchone()[0]
        return _row(c, pid)

def refund(payment_id: int, reason: str = "") -> dict:
    with conn() as c: pay = _row(c, payment_id)
    if pay["kind"] == "subscription":
        if pay["status"] != "active": raise ValueError("only active subscriptions can be cancelled")
        api("POST", f"/v1/billing/subscriptions/{pay['paypal_id']}/cancel", {"reason": (reason or "Cancelled by merchant")[:128]})
        return _set_status(pay["paypal_id"], "cancelled", f"PayPal subscription cancelled: {reason or 'by merchant'}")
    if pay["kind"] == "invoice" and not pay["capture_id"]:
        sync(payment_id);
        with conn() as c: pay = _row(c, payment_id)
    if pay["status"] not in ("completed", "paid") or not pay["capture_id"]:
        raise ValueError("only captured payments can be refunded")
    r = api("POST", f"/v2/payments/captures/{pay['capture_id']}/refund",
            {"note_to_payer": (reason or "Refund issued")[:255]}, idempotency_key=f"refund-{pay['capture_id']}")
    return _set_status(pay["paypal_id"], "refunded", f"PayPal refund {r.get('id')} ({r.get('status')}) for ${pay['amount']:,.2f}")

def sync(payment_id: int) -> dict:
    """Pull the latest state from PayPal (useful locally where webhooks can't reach you)."""
    with conn() as c: pay = _row(c, payment_id)
    ppid = pay["paypal_id"]
    if pay["kind"] == "order":
        o = api("GET", f"/v2/checkout/orders/{ppid}")
        if o.get("status") == "COMPLETED" and pay["status"] not in SETTLED:
            cap = o["purchase_units"][0].get("payments", {}).get("captures", [{}])[0]
            return settle(ppid, "completed", capture_id=cap.get("id"), source="sync")
    elif pay["kind"] == "subscription":
        s = api("GET", f"/v1/billing/subscriptions/{ppid}")
        if s.get("status") == "ACTIVE": return settle(ppid, "active", source="sync")
        if s.get("status") in ("CANCELLED", "SUSPENDED", "EXPIRED"): return _set_status(ppid, "cancelled", f"Subscription {s['status'].lower()}")
    else:
        inv = api("GET", f"/v2/invoicing/invoices/{ppid}")
        if inv.get("status") in ("PAID", "MARKED_AS_PAID"):
            txn = ((inv.get("payments") or {}).get("transactions") or [{}])[0]
            return settle(ppid, "paid", capture_id=txn.get("payment_id"), source="sync")
        if inv.get("status") == "CANCELLED": return _set_status(ppid, "cancelled", "Invoice cancelled")
    with conn() as c: return _row(c, payment_id)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def verify_webhook(headers, event: dict) -> bool:
    wid = os.environ.get("PAYPAL_WEBHOOK_ID")
    if not wid: return False
    h = {k.lower(): v for k, v in headers.items()}
    res = api("POST", "/v1/notifications/verify-webhook-signature", {
        "auth_algo": h.get("paypal-auth-algo"), "cert_url": h.get("paypal-cert-url"),
        "transmission_id": h.get("paypal-transmission-id"), "transmission_sig": h.get("paypal-transmission-sig"),
        "transmission_time": h.get("paypal-transmission-time"), "webhook_id": wid, "webhook_event": event})
    return res.get("verification_status") == "SUCCESS"

def handle_webhook(headers, event: dict) -> dict:
    ensure()
    if not verify_webhook(headers, event):
        raise PermissionError("webhook signature verification failed (is PAYPAL_WEBHOOK_ID set?)")
    eid, et, res = event.get("id"), event.get("event_type", ""), event.get("resource") or {}
    with conn() as c:
        if c.execute("select 1 from paypal_webhook_events where id=?", (eid,)).fetchone():
            return {"ok": True, "duplicate": True}
        c.execute("insert into paypal_webhook_events values(?,?,1,?)", (eid, et, now()))
    try:
        if et == "PAYMENT.CAPTURE.COMPLETED":
            oid = (res.get("supplementary_data") or {}).get("related_ids", {}).get("order_id")
            if oid: settle(oid, "completed", capture_id=res.get("id"), source="webhook")
        elif et == "BILLING.SUBSCRIPTION.ACTIVATED":
            settle(res.get("id"), "active", payer_email=(res.get("subscriber") or {}).get("email_address"), source="webhook")
        elif et in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED", "BILLING.SUBSCRIPTION.EXPIRED"):
            _set_status(res.get("id"), "cancelled", f"Webhook: {et}")
        elif et == "PAYMENT.SALE.COMPLETED" and res.get("billing_agreement_id"):
            with conn() as c:
                pay = _by_paypal_id(c, res["billing_agreement_id"])
                if pay: _event(c, pay["lead_id"], "paypal_renewal", f"Subscription payment received: ${float(res.get('amount', {}).get('total', 0)):,.2f}")
        elif et == "INVOICING.INVOICE.PAID":
            inv = res.get("invoice") or res
            txn = ((inv.get("payments") or {}).get("transactions") or [{}])[0]
            settle(inv.get("id"), "paid", capture_id=txn.get("payment_id"), source="webhook")
        elif et in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
            with conn() as c:
                r = c.execute("select paypal_id from payments where capture_id=?", (res.get("id") or "",)).fetchone()
            if not r:
                up = next((l["href"] for l in res.get("links", []) if l.get("rel") == "up"), "")
                with conn() as c:
                    r = c.execute("select paypal_id from payments where capture_id=?", (up.rsplit("/", 1)[-1],)).fetchone()
            if r: _set_status(r[0], "refunded", f"Webhook: {et}")
    except LookupError:
        pass  # event for a payment this instance didn't create
    return {"ok": True, "event_type": et}


# ---------------------------------------------------------------------------
# Reporting (used by the Payments view and Wren)
# ---------------------------------------------------------------------------

def list_payments() -> list:
    ensure()
    with conn() as c:
        rows = c.execute("""select p.*, l.company, l.contact_email, l.domain from payments p join leads l on l.id=p.lead_id
                            order by p.updated_at desc""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try: d["age_days"] = (datetime.now(timezone.utc) - datetime.fromisoformat(d["created_at"])).days
        except Exception: d["age_days"] = 0
        out.append(d)
    return out

def summary() -> dict:
    items = list_payments()
    collected = sum(p["amount"] for p in items if p["status"] in ("completed", "paid"))
    mrr = sum(p["amount"] for p in items if p["status"] == "active")
    outstanding = [p for p in items if p["status"] in OPEN]
    return {"collected": round(collected, 2), "mrr": round(mrr, 2), "outstanding_total": round(sum(p["amount"] for p in outstanding), 2),
            "outstanding": outstanding, "refunded": round(sum(p["amount"] for p in items if p["status"] == "refunded"), 2),
            "count": len(items), "env": env(), "configured": configured()}

def draft_reminder(payment_id: int, pay_url: Optional[str] = None) -> dict:
    """AI collections agent: write a tone-aware payment reminder and queue it for human approval."""
    with conn() as c:
        pay = _row(c, payment_id)
        l = dict(c.execute("select * from leads where id=?", (pay["lead_id"],)).fetchone())
    if pay["status"] not in OPEN: raise ValueError("this payment is not outstanding")
    url = pay_url or pay["approve_url"] or ""
    try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(pay["created_at"])).days
    except Exception: age = 0
    amount = f"${pay['amount']:,.2f}{'/month' if pay['interval'] == 'month' else ''}"
    subject = body = None
    try:
        import agent
        sysmsg = ("You are a collections assistant for a B2B services agency. Write a short, warm, professional payment reminder. "
                  "Escalate tone gently with age: <7 days friendly nudge, 7-21 days clear reminder, >21 days firm but polite and offer a call. "
                  "Never threaten, never invent facts, discounts or deadlines. Include the payment link exactly once. "
                  'Return JSON only: {"subject": "...", "body": "..."}')
        user = json.dumps({"company": l["company"], "contact": l.get("contact_name"), "item": pay["tier_name"], "amount": amount,
                           "days_outstanding": age, "payment_kind": pay["kind"], "payment_link": url, "sender_brand": brand()})
        out = agent.llm(sysmsg, user, max_tokens=400, temperature=0.4)
        if isinstance(out, dict) and out.get("subject") and out.get("body"):
            subject, body = str(out["subject"])[:200], str(out["body"])[:4000]
            if url and url not in body: body += f"\n\nPay securely with PayPal: {url}"
    except Exception:
        pass
    ai = bool(subject)
    if not ai:
        hi = f"Hi {l['contact_name'].split()[0]}," if l.get("contact_name") else "Hi there,"
        subject = f"Quick reminder: {pay['tier_name']} ({amount})"
        body = (f"{hi}\n\nJust a friendly reminder that the {pay['tier_name']} engagement ({amount}) is ready to start "
                f"as soon as payment is complete. You can pay securely with PayPal here:\n{url}\n\n"
                f"Happy to jump on a quick call if anything in the scope needs adjusting.\n\nThanks,\n{brand()}")
    with conn() as c:
        c.execute("insert into emails(lead_id,approach,step,subject,body,status) values(?,?,?,?,?,'draft')",
                  (pay["lead_id"], "PAYPAL_REMINDER", 0, subject, body))
        eid = c.execute("select last_insert_rowid()").fetchone()[0]
        _event(c, pay["lead_id"], "paypal_reminder", f"{'AI' if ai else 'Template'} payment reminder drafted ({age}d outstanding); awaiting approval")
    return {"email_id": eid, "subject": subject, "body": body, "ai": ai}
