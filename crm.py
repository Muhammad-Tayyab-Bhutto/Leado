"""CRM API + UI layer over app.py (reuses its schema, scoring, drafting, compliance). Single-tenant, token-authenticated."""
import errno, hmac, json, os, re, smtplib, sys, threading, urllib.parse
from datetime import date, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app, agent, sandbox, proposal_engine, copilot, workforce, growth, paypal, paypal_web, ops
from app import conn, STATUSES, SERVICES, now

TOKEN = os.environ.get("SOFTORASYSTEMS_TOKEN", "")
ADDRESS = os.environ.get("SOFTORASYSTEMS_ADDRESS", "")
PH = "[YOUR PHYSICAL BUSINESS ADDRESS]"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CLIENT_STATUSES = {"prospect", "active", "paused", "churned"}
SORTS = {"score": "score desc, id desc", "newest": "coalesce(discovered_at,researched_at) desc, id desc", "name": "company collate nocase"}
FIELDS = {"description": "s", "contact_name": "s", "contact_title": "s", "contact_email": "email", "industry": "s", "city": "s",
          "country": "s", "problem": "s", "est_project_value": "money", "monthly_retainer": "money", "actual_mrr": "money",
          "contract_start": "date", "acquired_at": "date", "client_status": "cs"}

def migrate():
    app.init()
    with conn() as c:
        have = {r[1] for r in c.execute("pragma table_info(leads)")}
        for col, typ in [("description","TEXT"),("discovered_at","TEXT"),("est_project_value","INT"),("monthly_retainer","INT"),
                         ("contract_start","TEXT"),("acquired_at","TEXT"),("client_status","TEXT")]:
            if col not in have: c.execute(f"alter table leads add column {col} {typ}")
        if "status" not in {r[1] for r in c.execute("pragma table_info(emails)")}:
            c.execute("alter table emails add column status TEXT DEFAULT 'draft'")
        c.execute("update leads set discovered_at=researched_at where discovered_at is null")
    agent.ensure()
    proposal_engine.init_proposals()
    workforce.ensure()
    growth.ensure()
    paypal.ensure()

def g(q, k): return (q.get(k) or [""])[0].strip()
def log(c, i, kind, detail=""): c.execute("insert into events(lead_id,kind,detail,at) values(?,?,?,?)", (i, kind, str(detail)[:500], now()))
def get_lead(c, i):
    r = c.execute("select * from leads where id=?", (i,)).fetchone()
    if not r: raise LookupError("lead not found")
    return r

def where(q):
    w, a = ["1=1"], []
    if g(q, "q"):
        w.append("(company like ? or domain like ? or contact_name like ? or contact_email like ?)"); a += [f"%{g(q,'q')}%"] * 4
    for k in ("country", "industry", "service", "status"):
        if g(q, k): w.append(f"{k}=?"); a.append(g(q, k))
    if g(q, "min"): w.append("score>=?"); a.append(int(g(q, "min")))
    if g(q, "since"): date.fromisoformat(g(q, "since")); w.append("coalesce(discovered_at,researched_at)>=?"); a.append(g(q, "since"))
    return " and ".join(w), a

def clean(k, v):
    if v is None or v == "": return None
    t = FIELDS[k]
    if t == "money":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 10**9: raise ValueError(f"{k} must be a non-negative number")
        return int(v)
    if t == "date":
        try: date.fromisoformat(str(v))
        except ValueError: raise ValueError(f"{k} must be YYYY-MM-DD")
        return str(v)
    if t == "email":
        v = str(v).strip().lower()
        if not EMAIL_RE.match(v) or len(v) > 254: raise ValueError("invalid email")
        return v
    if t == "cs":
        if v not in CLIENT_STATUSES: raise ValueError("client_status must be one of " + ", ".join(sorted(CLIENT_STATUSES)))
        return v
    v = str(v).strip()
    if len(v) > 5000: raise ValueError(f"{k} too long")
    return v

R = []
def route(m, pat):
    def d(f): R.append((m, re.compile("^" + pat + "$"), f)); return f
    return d

@route("GET", "/api/stats")
def stats(q, b):
    with conn() as c:
        one = lambda s: c.execute(s).fetchone()[0]
        return {"discovered": one("select count(*) from leads"),
            "qualified": one("select count(*) from leads where score>=60 and status not in ('LOST','OPTED_OUT')"),
            "high": one("select count(*) from leads where score>=75 and status not in ('LOST','OPTED_OUT')"),
            "ready": one("select count(distinct lead_id) from emails where status='draft'"),
            "sent": one("select count(*) from emails where status='sent'"),
            "positive": one("select count(distinct lead_id) from events where kind='reply_positive'"),
            "meetings": one("select count(*) from leads where status in ('MEETING','PROPOSAL','WON') or id in (select lead_id from events where kind='status' and detail='MEETING')"),
            "won": one("select count(*) from leads where status='WON'"),
            "mrr": one("select coalesce(sum(actual_mrr),0) from leads where status='WON'"),
            "pipeline_mrr": one("select coalesce(sum(monthly_retainer),0) from leads where status not in ('WON','LOST','OPTED_OUT')")}

@route("GET", "/api/facets")
def facets(q, b):
    with conn() as c:
        return {k: [r[0] for r in c.execute(f"select distinct {k} from leads where {k} is not null and {k}!='' order by 1")]
                for k in ("country", "industry", "service")}

@route("GET", "/api/leads")
def list_leads(q, b):
    w, a = where(q)
    page = max(1, int(g(q, "page") or 1)); size = min(200, max(1, int(g(q, "size") or 25)))
    order = SORTS.get(g(q, "sort") or "score")
    if not order: raise ValueError("sort must be score, newest or name")
    with conn() as c:
        total = c.execute(f"select count(*) from leads where {w}", a).fetchone()[0]
        rows = c.execute(f"select * from leads where {w} order by {order} limit ? offset ?", a + [size, (page - 1) * size]).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}

@route("GET", "/api/leads/(\\d+)")
def lead_detail(q, b, i):
    with conn() as c:
        l = dict(get_lead(c, i))
        em = [dict(r) for r in c.execute("select * from emails where lead_id=? order by step, approach", (i,))]
        ev = [dict(r) for r in c.execute("select * from events where lead_id=? order by id desc limit 100", (i,))]
        sup = c.execute("select 1 from suppression where email=?", ((l["contact_email"] or "").lower(),)).fetchone() is not None
    return {"lead": l, "emails": em, "events": ev, "suppressed": sup}

@route("PATCH", "/api/leads/(\\d+)")
def patch_lead(q, b, i):
    bad = set(b) - set(FIELDS)
    if bad or not b: raise ValueError("unknown or empty fields: " + ", ".join(sorted(bad)))
    vals = {k: clean(k, v) for k, v in b.items()}
    if "contact_email" in vals: vals["email_status"] = "unknown"
    with conn() as c:
        get_lead(c, i)
        c.execute(f"update leads set {','.join(k + '=?' for k in vals)} where id=?", [*vals.values(), i]); log(c, i, "update", ", ".join(sorted(b)))
    return {"ok": True}

@route("POST", "/api/leads/(\\d+)/status")
def set_status(q, b, i):
    s = b.get("status")
    if s not in STATUSES: raise ValueError("invalid status")
    with conn() as c:
        l = get_lead(c, i)
        appr = 1 if s == "APPROVED" else 0 if s in ("NEW", "RESEARCHED", "QUALIFIED", "EMAIL_DRAFTED", "LOST", "OPTED_OUT") else l["approved"]
        c.execute("update leads set status=?, approved=? where id=?", (s, appr, i))
        if s == "WON": c.execute("update leads set acquired_at=coalesce(acquired_at,?), client_status=coalesce(client_status,'active') where id=?", (date.today().isoformat(), i))
        if s == "OPTED_OUT" and l["contact_email"]:
            c.execute("insert or replace into suppression values(?,?,?)", (l["contact_email"].lower(), "opt-out", now()))
        log(c, i, "status", s)
    return {"ok": True}

@route("POST", "/api/leads/(\\d+)/note")
def note(q, b, i):
    t = str(b.get("text", "")).strip()
    if not t or len(t) > 2000: raise ValueError("note text required (max 2000)")
    with conn() as c: get_lead(c, i); log(c, i, "note", t)
    return {"ok": True}

@route("POST", "/api/leads/(\\d+)/reply")
def reply(q, b, i):
    k = b.get("kind")
    if k not in ("positive", "neutral", "optout"): raise ValueError("kind must be positive, neutral or optout")
    with conn() as c:
        l = get_lead(c, i)
        if k == "optout":
            if l["contact_email"]: c.execute("insert or replace into suppression values(?,?,?)", (l["contact_email"].lower(), "opt-out", now()))
            c.execute("update leads set status='OPTED_OUT', approved=0 where id=?", (i,))
        else: c.execute("update leads set status='REPLIED' where id=?", (i,))
        log(c, i, {"positive": "reply_positive", "neutral": "reply", "optout": "optout"}[k], b.get("note", ""))
    return {"ok": True}

@route("POST", "/api/leads/(\\d+)/draft")
def make_drafts(q, b, i):
    with conn() as c: get_lead(c, i)
    app.save_drafts(i)  # raises ValueError when no evidence-backed problem
    with conn() as c:
        if ADDRESS: c.execute("update emails set body=replace(body,?,?) where lead_id=? and status='draft'", (PH, ADDRESS, i))
        log(c, i, "drafted", "3 approaches + follow-ups")
    return {"ok": True}

@route("GET", "/api/emails")
def list_emails(q, b):
    s = g(q, "status") or "draft"
    if s not in ("draft", "approved", "rejected", "sent"): raise ValueError("bad status")
    with conn() as c:
        return {"items": [dict(r) for r in c.execute("select e.id,e.lead_id,e.approach,e.step,e.subject,e.status,l.company,l.score from emails e join leads l on l.id=e.lead_id where e.status=? and e.step=0 order by l.score desc limit 200", (s,))]}

@route("PATCH", "/api/emails/(\\d+)")
def patch_email(q, b, i):
    act = b.get("action")
    if act not in (None, "approve", "reject"): raise ValueError("action must be approve or reject")
    with conn() as c:
        e = c.execute("select * from emails where id=?", (i,)).fetchone()
        if not e: raise LookupError("email not found")
        if e["status"] == "sent": raise ValueError("sent emails are immutable")
        if "subject" in b or "body" in b:
            s = str(b.get("subject", e["subject"])).strip(); t = str(b.get("body", e["body"])).strip()
            if not s or len(s) > 300 or not t or len(t) > 20000: raise ValueError("subject (max 300) and body (max 20000) are required")
            c.execute("update emails set subject=?, body=?, status='draft', approved=0 where id=?", (s, t, i)); e = c.execute("select * from emails where id=?", (i,)).fetchone()
        if act == "approve":
            if "unsubscribe" not in e["body"].lower(): raise ValueError("email must include an opt-out line")
            c.execute("update emails set status='approved', approved=1 where id=?", (i,))
            c.execute("update leads set approved=1, status=case when status in ('NEW','RESEARCHED','QUALIFIED','EMAIL_DRAFTED') then 'APPROVED' else status end where id=?", (e["lead_id"],))
        elif act == "reject": c.execute("update emails set status='rejected', approved=0 where id=?", (i,))
        log(c, e["lead_id"], "email_" + (act or "edited"), f"{e['approach']} step {e['step']}")
    return {"ok": True}

def add_bdays(d, n):
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5: n -= 1
    return d

def smtp_send(to, subj, body):
    env = {k: os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")}
    if not all(env.values()): raise NotImplementedError("SMTP not configured (set SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM)")
    m = EmailMessage(); m["From"], m["To"], m["Subject"] = env["SMTP_FROM"], to, subj
    m["List-Unsubscribe"] = f"<mailto:{env['SMTP_FROM']}?subject=unsubscribe>"; m.set_content(body)
    try:
        with smtplib.SMTP(env["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=20) as s:
            s.starttls(); s.login(env["SMTP_USER"], env["SMTP_PASS"]); s.send_message(m)
    except (OSError, smtplib.SMTPException) as ex: raise RuntimeError(f"SMTP delivery failed: {ex}")

@route("POST", "/api/emails/(\\d+)/send")
def send_email(q, b, i):
    if b.get("confirm") is not True: raise ValueError("explicit confirmation required")
    with conn() as c:
        e = c.execute("select * from emails where id=?", (i,)).fetchone()
        if not e: raise LookupError("email not found")
        l = get_lead(c, e["lead_id"])
        sent0 = c.execute("select approach from emails where lead_id=? and step=0 and status='sent'", (l["id"],)).fetchall()
    if e["status"] != "approved": raise ValueError("email must be approved first")
    reminder = e["approach"] == "PAYPAL_REMINDER"  # collections email to an existing client, not cold outreach
    if not reminder and e["step"] == 0 and sent0: raise ValueError("an initial email was already sent to this lead")
    if not reminder and e["step"] > 0 and not sent0: raise ValueError("send the initial email first")
    if e["due_date"] and e["due_date"] > date.today().isoformat(): raise ValueError(f"follow-up not due until {e['due_date']}")
    errs = app.can_send(l["id"], bool(ADDRESS) and PH not in e["body"])
    if errs: raise ValueError("; ".join(errs))
    if b.get("manual") is not True: smtp_send(l["contact_email"], e["subject"], e["body"])
    with conn() as c:
        c.execute("update emails set status='sent', sent_at=? where id=?", (now(), i))
        if not reminder: c.execute("update leads set status=? where id=?", ("CONTACTED" if e["step"] == 0 else "FOLLOW_UP", l["id"]))
        if e["step"] == 0 and not reminder:
            for n, d in zip((1, 2, 3), app.FOLLOWUP_DAYS[1:]):
                c.execute("update emails set due_date=? where lead_id=? and step=?", (add_bdays(date.today(), d).isoformat(), l["id"], n))
        log(c, l["id"], "email_sent", f"{e['approach']} step {e['step']} ({'manual' if b.get('manual') else 'smtp'})")
    return {"ok": True}

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
@route("GET", "/api/ai/status")
def ai_status(q, b): return agent.usage()

@route("POST", "/api/leads/import-domains")
def import_domains(q, b):
    ds = b.get("domains")
    if not isinstance(ds, list) or not 0 < len(ds) <= 50: raise ValueError("domains must be a list of 1-50 items")
    added = skipped = 0
    with conn() as c:
        for d in ds:
            d = app.domain_of(str(d).strip().lower())
            if not DOMAIN_RE.match(d) or len(d) > 253: skipped += 1; continue
            cur = c.execute("insert or ignore into leads(domain,company,website,status,discovered_at) values(?,?,?,'NEW',?)", (d, d, "https://" + d, now()))
            added += cur.rowcount; skipped += 1 - cur.rowcount
    return {"added": added, "skipped": skipped}

@route("POST", "/api/leads/(\\d+)/research")
def ai_research(q, b, i): return agent.research(i)

@route("POST", "/api/leads/(\\d+)/ai-draft")
def ai_draft(q, b, i): return agent.ai_draft(i)

@route("POST", "/api/leads/(\\d+)/classify-reply")
def classify(q, b, i):
    with conn() as c: get_lead(c, i)
    return agent.classify_reply(b.get("text", ""))

@route("POST", "/api/ai/research-batch")
def research_batch(q, b):
    n = min(5, max(1, int(b.get("limit", 5))))
    with conn() as c: ids = [r[0] for r in c.execute("select id from leads where status='NEW' and description is null order by id limit ?", (n,))]
    res = []
    for i in ids:
        try: res.append({"id": i, "ok": True, **agent.research(i)})
        except NotImplementedError: raise
        except (RuntimeError, ValueError, LookupError) as ex:
            res.append({"id": i, "ok": False, "error": str(ex)})
            if "cap reached" in str(ex): break
    return {"results": res}

@route("GET", "/api/export.csv")
def export(q, b):
    w, a = where(q); return app.export_csv(w, a), "text/csv"
R.append(("GET", re.compile("^/export.csv$"), export))

# ============================================================================
# Dealwright PROPOSALS, BLAST RADIUS SANDBOX & WREN COPILOT ROUTES
# ============================================================================

@route("GET", "/api/proposals")
def proposals_list(q, b):
    return {"items": proposal_engine.list_proposals()}

@route("GET", "/api/leads/(\\d+)/proposal")
def lead_proposal_get(q, b, i):
    p = proposal_engine.get_proposal(i)
    if not p: return {"error": "No proposal generated yet"}
    return p

# ============================================================================
# PAYPAL QUOTE-TO-CASH ROUTES (authenticated)
# ============================================================================

_REQ = threading.local()

def base_url(h=None):
    if os.environ.get("PUBLIC_BASE_URL"): return os.environ["PUBLIC_BASE_URL"].rstrip("/")
    if h is None: return f"http://localhost:{PORT}"
    proto = (h.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
    host = h.headers.get("X-Forwarded-Host") or h.headers.get("Host") or f"localhost:{PORT}"
    return f"{proto}://{host}"

# ============================================================================
# TEAM & PROJECTS (admin)
# ============================================================================

@route("GET", "/api/team")
def team_list(q, b): return {"items": ops.list_members(include_inactive=g(q, "all") == "1"), "summary": ops.summary()}

@route("POST", "/api/team")
def team_add(q, b): return ops.add_member(b)

@route("PATCH", "/api/team/(\\d+)")
def team_patch(q, b, i): return ops.update_member(i, b)

@route("GET", "/api/projects")
def projects_list(q, b): return {"items": ops.list_projects(g(q, "status") or None), "summary": ops.summary()}

@route("POST", "/api/projects")
def projects_add(q, b): return ops.create_project(b)

@route("POST", "/api/projects/auto-assign")
def projects_auto(q, b): return ops.auto_assign()

@route("GET", "/api/projects/(\\d+)")
def project_get(q, b, i): return ops.get_project(i)

@route("PATCH", "/api/projects/(\\d+)")
def project_patch(q, b, i): return ops.update_project(i, b)

@route("GET", "/api/paypal/status")
def paypal_status(q, b): return paypal.status()

@route("GET", "/api/payments")
def payments_list(q, b): return {"items": paypal.list_payments(), "summary": {k: v for k, v in paypal.summary().items() if k != "outstanding"}}

@route("POST", "/api/proposals/(\\d+)/paylink")
def proposal_paylink(q, b, i): return paypal.pay_link(i, getattr(_REQ, "base", None) or base_url())

@route("POST", "/api/leads/(\\d+)/invoice")
def lead_invoice(q, b, i):
    if b.get("confirm") is not True: raise ValueError("explicit confirmation required")
    return paypal.send_invoice(i, int(b.get("tier", 0)))

@route("POST", "/api/payments/(\\d+)/refund")
def payment_refund(q, b, i):
    if b.get("confirm") is not True: raise ValueError("explicit confirmation required")
    return paypal.refund(i, str(b.get("reason", ""))[:200])

@route("POST", "/api/payments/(\\d+)/sync")
def payment_sync(q, b, i): return paypal.sync(i)

@route("POST", "/api/payments/(\\d+)/remind")
def payment_remind(q, b, i):
    with conn() as c:
        r = c.execute("select proposal_id from payments where id=?", (i,)).fetchone()
    url = None
    if r and r[0]:
        try: url = paypal.pay_link(r[0], getattr(_REQ, "base", None) or base_url())["url"]
        except LookupError: pass
    with conn() as c:
        k = c.execute("select kind from payments where id=?", (i,)).fetchone()
    return paypal.draft_reminder(i, None if (k and k[0] == "invoice") else url)

@route("POST", "/api/leads/(\\d+)/proposal")
def lead_proposal_gen(q, b, i):
    return proposal_engine.generate_proposal(i)

@route("PATCH", "/api/proposals/(\\d+)")
def proposal_patch(q, b, i):
    with conn() as c:
        for k in ("title", "status", "est_value", "timeline_weeks", "executive_summary"):
            if k in b:
                c.execute(f"UPDATE proposals SET {k}=?, updated_at=? WHERE id=?", (b[k], now(), i))
        r = c.execute("SELECT lead_id FROM proposals WHERE id=?", (i,)).fetchone()
        if r:
            return proposal_engine.get_proposal(r[0])
        return {"ok": True}

@route("GET", "/api/sandbox/status")
def sandbox_status_route(q, b):
    return sandbox.sandbox.verify_containment_proof()

@route("POST", "/api/leads/(\\d+)/sandbox-audit")
def sandbox_audit_lead(q, b, i):
    with conn() as c:
        lead = get_lead(c, i)
    target = lead["website"] or f"https://{lead['domain']}"
    res = sandbox.sandbox.run_isolated_audit(target)
    log(c, i, "sandbox_audit", f"Audit executed with containment status {res.get('data', {}).get('containment', 'UNKNOWN')}")
    return res

@route("POST", "/api/copilot/chat")
def copilot_chat_route(q, b):
    msg = b.get("message", "")
    return copilot.vio.handle_query(msg)

@route("GET", "/api/voice/state")
def voice_state(q, b):
    return workforce.state()

@route("POST", "/api/voice/talk")
def voice_talk(q, b):
    return workforce.talk(b.get("utterance") or b.get("message") or "", b.get("agent"))

@route("POST", "/api/voice/connectors")
def voice_connector(q, b):
    cid = (b.get("id") or "").strip()
    if not cid:
        raise ValueError("connector id required")
    enabled = b.get("enabled") if "enabled" in b else None
    config = b.get("config") if "config" in b else None
    return workforce.set_connector(cid, enabled, config)

@route("GET", "/api/growth/desk")
def growth_desk(q, b):
    return growth.desk()

@route("POST", "/api/growth/leads")
def growth_create(q, b):
    return growth.create_lead(b)

@route("GET", "/api/growth/leads/(\\d+)")
def growth_get(q, b, i):
    return growth.get_lead(i)

@route("PATCH", "/api/growth/leads/(\\d+)")
def growth_patch(q, b, i):
    return growth.update_lead(i, b)

@route("POST", "/api/growth/leads/(\\d+)/opt-out")
def growth_opt_out(q, b, i):
    return growth.opt_out(i)

@route("POST", "/api/growth/leads/(\\d+)/drafts")
def growth_drafts(q, b, i):
    return growth.generate_drafts(i)

@route("POST", "/api/growth/leads/(\\d+)/touch")
def growth_touch(q, b, i):
    return growth.mark_touch(i, b.get("step") or 1, b.get("variant") or "A")

@route("GET", "/api/copilot/suggestions")
def copilot_suggestions(q, b):
    return {
        "suggestions": [
            "Give me an executive pipeline briefing",
            "Verify Blast Radius Zero sandbox containment",
            "Generate proposal for top scoring lead",
            "What services yield the highest recurring MRR?",
            "Run discovery for high-intent SaaS leads"
        ]
    }

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _out(self, code, body, ct="application/json"):
        raw = body.encode() if isinstance(body, str) and ct != "application/json" else (json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(code); self.send_header("Content-Type", ct + "; charset=utf-8"); self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        if ct == "text/html": self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(raw)
    def _handle(self, m):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if m == "GET" and u.path == "/": 
            ui_content = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
            return self._out(200, ui_content, "text/html")
        if m == "GET" and u.path == "/favicon.ico": return self._out(204, "", "image/x-icon")
        if u.path.startswith("/pay/") or u.path == "/api/paypal/webhook": return self._public(m, u, q)
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + TOKEN).encode()): return self._out(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000: raise ValueError("request too large")
            body = json.loads(self.rfile.read(n) or b"{}") if m != "GET" else {}
            if not isinstance(body, dict): raise ValueError("JSON object expected")
            _REQ.base = base_url(self)
            for rm, pat, f in R:
                mo = pat.match(u.path)
                if rm == m and mo:
                    res = f(q, body, *[int(x) for x in mo.groups()])
                    return self._out(200, *res) if isinstance(res, tuple) else self._out(200, res)
            self._out(404, {"error": "not found"})
        except LookupError as ex: self._out(404, {"error": str(ex)})
        except ValueError as ex: self._out(400, {"error": str(ex)})
        except NotImplementedError as ex: self._out(501, {"error": str(ex)})
        except RuntimeError as ex: self._out(502, {"error": str(ex)})
        except Exception as ex: print("ERROR", type(ex).__name__, ex, file=sys.stderr); self._out(500, {"error": "internal error"})
    def _redirect(self, url):
        self.send_response(303); self.send_header("Location", url); self.send_header("Cache-Control", "no-store"); self.end_headers()
    def _public(self, m, u, q):
        """Unauthenticated surface: token-scoped client payment pages + PayPal webhooks."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000: raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b""
            if u.path == "/api/paypal/webhook":
                if m != "POST": return self._out(405, {"error": "POST only"})
                try: return self._out(200, paypal.handle_webhook(dict(self.headers), json.loads(raw or b"{}")))
                except PermissionError as ex: return self._out(400, {"error": str(ex)})
            mo = re.match(r"^/pay/([A-Za-z0-9_-]{16,64})(/checkout|/return)?$", u.path)
            if not mo: return self._out(404, paypal_web.page("Not found", paypal_web.notice(False, "This payment link is not valid.")), "text/html")
            tok, sub = mo.group(1), mo.group(2) or ""
            try:
                if m == "GET" and sub == "": return self._out(200, paypal_web.proposal_page(tok), "text/html")
                if m == "POST" and sub == "/checkout":
                    form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
                    return self._redirect(paypal.start_checkout(tok, int((form.get("tier") or ["0"])[0]), base_url(self)))
                if m == "GET" and sub == "/return": return self._out(200, paypal_web.thanks(tok, paypal.complete_return(tok, q)), "text/html")
            except LookupError as ex:
                return self._out(404, paypal_web.page("Not found", paypal_web.notice(False, str(ex))), "text/html")
            except (ValueError, RuntimeError, NotImplementedError) as ex:
                print("PAYPAL", type(ex).__name__, ex, file=sys.stderr)
                return self._out(502, paypal_web.proposal_page(tok, paypal_web.notice(False, f"Payment could not be completed: {ex}")), "text/html")
            self._out(405, {"error": "method not allowed"})
        except Exception as ex:
            print("ERROR", type(ex).__name__, ex, file=sys.stderr); self._out(500, {"error": "internal error"})
    do_GET = lambda s: s._handle("GET")
    do_POST = lambda s: s._handle("POST")
    do_PATCH = lambda s: s._handle("PATCH")

HOST = os.environ.get("SOFTORA_HOST", "127.0.0.1")
PORT = int(os.environ.get("SOFTORA_PORT", "8000"))
PORT_TRIES = 20  # how many consecutive ports to try when the requested one is busy

def make_server(host=None, port=None):
    """Bind the first free port at or after `port`. A stale server on 8000 no longer crashes startup."""
    host, start, last = host or HOST, int(port if port is not None else PORT), None
    for p in range(start, start + PORT_TRIES):
        try:
            return ThreadingHTTPServer((host, p), H)
        except OSError as ex:
            if ex.errno != errno.EADDRINUSE: raise
            last = ex
            print(f"Port {p} is already in use, trying {p + 1}...", file=sys.stderr)
    raise SystemExit(f"No free port in {start}-{start + PORT_TRIES - 1} (last error: {last}). "
                     f"Stop the other server or pass a port: python app.py serve <PORT>")

def serve(host=None, port=None):
    if len(TOKEN) < 16: raise SystemExit("Set SOFTORASYSTEMS_TOKEN (at least 16 characters) to start the CRM.")
    host, port = host or HOST, int(port or PORT)
    migrate()
    srv = make_server(host, port)
    actual = srv.server_address[1]
    print(f"Dealwright: http://{host}:{actual}")
    if actual != port: print(f"(requested port {port} was busy, using {actual})", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nDealwright stopped.")
    finally:
        srv.server_close()


# ============================================================================
# LEAD FINDER API ENDPOINTS
# ============================================================================

# Initialize lead finder when imported
try:
    import lead_finder
    from lead_finder.processor import LeadProcessor, init_all
    init_all()
except Exception as e:
    print(f"Warning: Could not initialize lead_finder: {e}", file=sys.stderr)

@route("GET", "/api/lead_finder/stats")
def lead_finder_stats(q, b):
    """Get lead finder statistics"""
    try:
        from lead_finder.database import conn as finder_conn, now
        
        with finder_conn() as c:
            today = now()[:10]
            today_row = c.execute("SELECT * FROM daily_stats WHERE day=?", (today,)).fetchone()
            
            total_requests = c.execute("SELECT SUM(requests_made) FROM daily_stats").fetchone()[0] or 0
            total_leads = c.execute("SELECT SUM(leads_found) FROM daily_stats").fetchone()[0] or 0
            total_imported = c.execute("SELECT SUM(leads_imported) FROM daily_stats").fetchone()[0] or 0
            total_errors = c.execute("SELECT SUM(errors) FROM daily_stats").fetchone()[0] or 0
            
            recent = [dict(r) for r in c.execute(
                "SELECT timestamp, source_type, action, company, result FROM discovery_log ORDER BY timestamp DESC LIMIT 20"
            )]
        
        return {
            "today": {
                "requests": today_row["requests_made"] if today_row else 0,
                "leads_found": today_row["leads_found"] if today_row else 0,
                "leads_imported": today_row["leads_imported"] if today_row else 0,
                "errors": today_row["errors"] if today_row else 0
            },
            "overall": {
                "requests": total_requests,
                "leads_found": total_leads,
                "leads_imported": total_imported,
                "errors": total_errors
            },
            "recent_activity": recent
        }
    except Exception as e:
        return {"error": str(e)}

@route("POST", "/api/lead_finder/discover")
def lead_finder_discover(q, b):
    """Run lead discovery"""
    try:
        processor = LeadProcessor()
        config = {
            "keywords": b.get("keywords"),
            "limit_per_source": b.get("limit_per_source", 50),
            "enrich": b.get("enrich", True),
            "deep_enrich": b.get("deep_enrich", False)
        }
        
        raw_leads = processor.discover_leads(config)
        result = processor.import_batch(raw_leads, config)
        
        return {
            "discovered": len(raw_leads),
            "imported": result["imported"],
            "errors": result["errors"]
        }
    except Exception as e:
        return {"error": str(e)}

@route("POST", "/api/lead_finder/enrich")
def lead_finder_enrich(q, b):
    """Enrich existing leads"""
    try:
        from datetime import datetime, timezone, timedelta
        processor = LeadProcessor()
        
        days = b.get("days", 7)
        limit = b.get("limit", 100)
        
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        
        with conn() as c:
            leads_to_enrich = c.execute(
                "SELECT id, domain, company, website FROM leads WHERE description IS NULL AND discovered_at >= ? ORDER BY id LIMIT ?",
                (cutoff, limit)
            ).fetchall()
        
        enriched_count = 0
        for lead in leads_to_enrich:
            lead_id, domain, company, website = lead
            
            try:
                for enricher in processor.enrichers[:2]:
                    result = enricher.enrich(domain or website, {"domain": domain, "company": company, "website": website})
                    if result and not result.get("error"):
                        update_data = {}
                        if "description" in result:
                            update_data["description"] = result["description"][:500]
                        if "industry" in result:
                            update_data["industry"] = result["industry"]
                        if "country" in result:
                            update_data["country"] = result["country"]
                        if "city" in result:
                            update_data["city"] = result["city"]
                        
                        if update_data:
                            with conn() as c:
                                c.execute(
                                    f"UPDATE leads SET {','.join(f'{k}=?' for k in update_data)} WHERE id=?",
                                    [update_data[k] for k in update_data] + [lead_id]
                                )
                            enriched_count += 1
                            break
            except Exception:
                continue
        
        return {"enriched": enriched_count, "total": len(leads_to_enrich)}
    except Exception as e:
        return {"error": str(e)}

@route("GET", "/api/lead_finder/status")
def lead_finder_status(q, b):
    """Get lead finder status"""
    try:
        from lead_finder.database import conn as finder_conn
        
        with finder_conn() as c:
            monitoring = c.execute(
                "SELECT * FROM monitoring_jobs WHERE job_type='discovery' AND status='active' LIMIT 1"
            ).fetchone()
        
        return {
            "monitoring_active": monitoring is not None,
            "monitoring_interval": monitoring["config"] if monitoring else None
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# BULK PROCESSING API ENDPOINTS
# ============================================================================

@route("POST", "/api/bulk/discover")
def bulk_discover(q, b):
    """Run bulk lead discovery with background job support"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        config = {
            "keywords": b.get("keywords"),
            "limit_per_source": b.get("limit_per_source", 50),
            "enrich": b.get("enrich", True),
            "deep_enrich": b.get("deep_enrich", False),
            "async": b.get("async", False)
        }
        
        # Run synchronously or asynchronously
        if config.get("async"):
            # Create background job
            job = bulk_engine.create_job("discovery", config)
            return {
                "job_id": job.id,
                "status": "queued",
                "message": "Discovery job created"
            }
        else:
            # Run immediately
            result = bulk_engine.run_discovery(config)
            return {
                "discovered": result["total_discovered"],
                "imported": 0,
                "errors": len(result.get("errors", [])),
                "sources": result.get("sources", {}),
                "leads": result.get("leads", [])
            }
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/enrich")
def bulk_enrich(q, b):
    """Run bulk lead enrichment"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        config = {
            "days": b.get("days", 7),
            "limit": b.get("limit", 100),
            "deep": b.get("deep", False),
            "async": b.get("async", False)
        }
        
        if config.get("async"):
            job = bulk_engine.create_job("enrichment", config)
            return {"job_id": job.id, "status": "queued"}
        else:
            result = bulk_engine.run_enrichment(config)
            return {
                "enriched": result["enriched"],
                "total": result["total"],
                "errors": result.get("errors", [])
            }
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/score")
def bulk_score(q, b):
    """Run bulk lead scoring"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        config = {
            "limit": b.get("limit", 100),
            "async": b.get("async", False)
        }
        
        if config.get("async"):
            job = bulk_engine.create_job("scoring", config)
            return {"job_id": job.id, "status": "queued"}
        else:
            result = bulk_engine.run_scoring(config)
            return {
                "scored": result["scored"],
                "total": result["total"],
                "errors": result.get("errors", [])
            }
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/pipeline")
def bulk_pipeline(q, b):
    """Run complete pipeline: discover -> enrich -> score -> import"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        config = {
            "keywords": b.get("keywords"),
            "limit_per_source": b.get("limit_per_source", 50),
            "enrich": b.get("enrich", True),
            "deep_enrich": b.get("deep_enrich", False),
            "async": b.get("async", False)
        }
        
        if config.get("async"):
            job = bulk_engine.create_job("full_pipeline", config)
            return {"job_id": job.id, "status": "queued"}
        else:
            result = bulk_engine.run_full_pipeline(config)
            return {
                "status": result.get("status", "completed"),
                "final_leads": result.get("final_leads", 0),
                "imported": result.get("imported", 0),
                "updated": result.get("updated", 0),
                "stages": result.get("stages", {}),
                "errors": result.get("total_errors", [])
            }
    except Exception as e:
        return {"error": str(e)}


@route("GET", "/api/bulk/jobs")
def bulk_jobs_list(q, b):
    """List all bulk processing jobs"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        job_type = q.get("type", [None])[0]
        status = q.get("status", [None])[0]
        
        jobs = bulk_engine.list_jobs(job_type, status)
        return {
            "jobs": [job.to_dict() for job in jobs],
            "total": len(jobs)
        }
    except Exception as e:
        return {"error": str(e)}


@route("GET", "/api/bulk/jobs/(.+)")
def bulk_job_status(q, b, job_id):
    """Get status of a specific bulk job"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        job = bulk_engine.get_job_status(job_id)
        if job:
            return job.to_dict()
        else:
            return {"error": "Job not found"}
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/jobs/(.+)/cancel")
def bulk_job_cancel(q, b, job_id):
    """Cancel a running bulk job"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        
        success = bulk_engine.cancel_job(job_id)
        if success:
            return {"ok": True, "message": "Job cancelled"}
        else:
            return {"ok": False, "error": "Could not cancel job"}
    except Exception as e:
        return {"error": str(e)}


@route("GET", "/api/bulk/stats")
def bulk_stats(q, b):
    """Get bulk processing statistics"""
    try:
        from lead_finder.bulk_processor import job_queue
        
        with finder_conn() as c:
            # Get daily stats
            today = now()[:10]
            today_row = c.execute(
                "SELECT * FROM daily_requests WHERE day=?", (today,)
            ).fetchone()
            
            # Get total stats
            total_requests = c.execute("SELECT SUM(count) FROM daily_requests").fetchone()[0] or 0
            
            # Get job counts
            total_jobs = c.execute("SELECT COUNT(*) FROM bulk_jobs").fetchone()[0] or 0
            completed_jobs = c.execute(
                "SELECT COUNT(*) FROM bulk_jobs WHERE status='completed'"
            ).fetchone()[0] or 0
            running_jobs = c.execute(
                "SELECT COUNT(*) FROM bulk_jobs WHERE status='running'"
            ).fetchone()[0] or 0
            failed_jobs = c.execute(
                "SELECT COUNT(*) FROM bulk_jobs WHERE status='failed'"
            ).fetchone()[0] or 0
        
        return {
            "today": {
                "requests": today_row[1] if today_row else 0,
                "limit": int(os.environ.get("DAILY_LIMIT", 1000))
            },
            "total": {
                "requests": total_requests,
                "jobs": total_jobs
            },
            "jobs": {
                "total": total_jobs,
                "completed": completed_jobs,
                "running": running_jobs,
                "failed": failed_jobs
            }
        }
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/monitor/start")
def bulk_monitor_start(q, b):
    """Start background bulk monitoring"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        from lead_finder.database import conn as finder_conn
        
        interval = b.get("interval", 3600)
        
        # Store monitoring config
        with finder_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO monitoring_jobs (job_type, config, status, created_at) VALUES (?, ?, ?, ?)",
                ("bulk_monitor", json.dumps({"interval": interval}), "active", now())
            )
        
        bulk_engine.start_background()
        return {"ok": True, "message": f"Background monitoring started (interval: {interval}s)"}
    except Exception as e:
        return {"error": str(e)}


@route("POST", "/api/bulk/monitor/stop")
def bulk_monitor_stop(q, b):
    """Stop background bulk monitoring"""
    try:
        from lead_finder.bulk_processor import bulk_engine
        from lead_finder.database import conn as finder_conn
        
        bulk_engine.stop_background()
        
        with finder_conn() as c:
            c.execute(
                "UPDATE monitoring_jobs SET status='inactive' WHERE job_type='bulk_monitor'"
            )
        
        return {"ok": True, "message": "Background monitoring stopped"}
    except Exception as e:
        return {"error": str(e)}


# Import the finder_conn for use in the endpoints
try:
    from lead_finder.database import conn as finder_conn
except ImportError:
    finder_conn = None
