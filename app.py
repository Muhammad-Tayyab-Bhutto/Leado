"""Softora lead engine: SQLite store, scoring, evidence-gated email drafts,
compliance gate, CSV export, and a stdlib dashboard. Stdlib only. Never auto-sends."""
import csv, io, json, re, sqlite3, sys, urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

load_dotenv()

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")  # always next to app.py, whatever folder you run from
STATUSES = ["NEW","RESEARCHED","QUALIFIED","EMAIL_DRAFTED","APPROVED","CONTACTED","FOLLOW_UP",
            "REPLIED","MEETING","PROPOSAL","WON","LOST","OPTED_OUT"]
SERVICES = {"web_app","saas","mobile","ai_agents","automation","erp_crm","cms","technical_seo","aeo","geo",
            "seo_content","cro","devops"}
PACKAGES = {"A":"Website Care","B":"SEO & AI Search Growth","C":"AI Automation","D":"SaaS & Software Maintenance"}
SERVICE_TO_PACKAGE = {"technical_seo":"B","aeo":"B","geo":"B","seo_content":"B","cro":"B","cms":"A",
    "automation":"C","ai_agents":"C","saas":"D","web_app":"D","mobile":"D","erp_crm":"D","devops":"D"}
FOLLOWUP_DAYS = [0, 3, 7, 14]  # business days after initial email
now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

def conn():
    c = sqlite3.connect(DB, timeout=30.0); c.row_factory = sqlite3.Row; return c

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, domain TEXT UNIQUE NOT NULL, company TEXT NOT NULL,
          website TEXT, industry TEXT, country TEXT, city TEXT, service TEXT, problem TEXT, evidence_url TEXT,
          researched_at TEXT, contact_name TEXT, contact_title TEXT, contact_email TEXT,
          email_status TEXT DEFAULT 'unknown', email_source TEXT, linkedin_url TEXT,
          f_relevance INT DEFAULT 0, f_problem INT DEFAULT 0, f_pay INT DEFAULT 0, f_recurring INT DEFAULT 0,
          f_access INT DEFAULT 0, f_growth INT DEFAULT 0, f_data INT DEFAULT 0,
          score INT DEFAULT 0, score_reason TEXT, package TEXT, est_mrr_low INT, est_mrr_high INT, actual_mrr INT DEFAULT 0,
          status TEXT DEFAULT 'NEW', approved INT DEFAULT 0, notes TEXT);
        CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY, lead_id INT, approach TEXT, step INT,
          subject TEXT, body TEXT, approved INT DEFAULT 0, sent_at TEXT, due_date TEXT);
        CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY, reason TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, lead_id INT, kind TEXT, detail TEXT, at TEXT);""")

def domain_of(url):
    h = urllib.parse.urlparse(url if "//" in url else "//"+url).netloc.lower()
    return h[4:] if h.startswith("www.") else h

# ---- scoring: weights from spec (25/20/15/15/10/10/5). Factors are 0.0-1.0 judgements, each must be justified.
WEIGHTS = {"f_relevance":25,"f_problem":20,"f_pay":15,"f_recurring":15,"f_access":10,"f_growth":10,"f_data":5}
def classify(s): return ("Excellent" if s>=90 else "High-priority" if s>=75 else "Potential" if s>=60
                         else "Low-priority" if s>=40 else "Do not prioritize")
def score(l):
    """Hard caps prevent inflation: no evidence URL => problem factor 0; unverified email => data factor <= 0.4."""
    f = {k: max(0.0, min(1.0, float(l.get(k) or 0)/ (1 if float(l.get(k) or 0)<=1 else 100))) for k in WEIGHTS}
    if not l.get("evidence_url"): f["f_problem"] = 0
    if l.get("email_status") != "verified": f["f_data"] = min(f["f_data"], 0.4)
    if not l.get("contact_email"): f["f_data"] = 0
    total = round(sum(WEIGHTS[k]*v for k,v in f.items()))
    if not l.get("evidence_url"): total = min(total, 59)  # unsupported leads can never be 'Potential+'
    why = "; ".join(f"{k[2:]} {round(WEIGHTS[k]*v,1)}/{WEIGHTS[k]}" for k,v in f.items())
    return total, f"{classify(total)} ({why})"

def price_range(package, size="small"):
    # Placeholder ranges for YOU to calibrate; not market research.
    base = {"A":(150,500),"B":(800,2500),"C":(1000,3500),"D":(1500,5000)}[package]
    return base

def upsert(l):
    for r in ("company","website"):
        if not l.get(r): raise ValueError(f"missing {r}")
    l["domain"] = domain_of(l["website"])
    if l.get("service") not in SERVICES: raise ValueError(f"service must be one of {sorted(SERVICES)}")
    l["package"] = SERVICE_TO_PACKAGE[l["service"]]
    l["est_mrr_low"], l["est_mrr_high"] = price_range(l["package"])
    l["score"], l["score_reason"] = score(l)
    l.setdefault("researched_at", now())
    cols = [k for k in l if k in COLS]
    with conn() as c:
        c.execute(f"INSERT INTO leads({','.join(cols)}) VALUES({','.join('?'*len(cols))}) "
                  f"ON CONFLICT(domain) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in cols if k!='domain')}",
                  [l[k] for k in cols])
COLS = {"domain","company","website","industry","country","city","service","problem","evidence_url","researched_at",
        "contact_name","contact_title","contact_email","email_status","email_source","linkedin_url","f_relevance",
        "f_problem","f_pay","f_recurring","f_access","f_growth","f_data","score","score_reason","package",
        "est_mrr_low","est_mrr_high","notes"}

# ---- email drafts: refuse without researched evidence (research must drive the message)
SIG = ("Muhammad Tayyab Bhutto\nFounder, Softora Systems\nhttps://softorasystems.com | contact@softorasystems.com\n"
       "{address}\n\nIf you'd rather not hear from me, reply \"unsubscribe\" and I won't contact you again.")
def draft(l, address="[YOUR PHYSICAL BUSINESS ADDRESS]"):
    if not (l["problem"] and l["evidence_url"]): raise ValueError("no evidence-backed problem; refusing to draft")
    hi = f"Hi {l['contact_name'].split()[0]}," if l.get("contact_name") else f"Hello {l['company']} team,"
    sig = SIG.format(address=address); pk = PACKAGES[l["package"]]
    obs = f"While looking at {l['website']}, I noticed: {l['problem']} (source: {l['evidence_url']})."
    A = (f"A quick note on {l['company']}'s site", f"{hi}\n\n{obs}\n\nSoftora can help with this ({l['service'].replace('_',' ')}). "
         f"I'd be glad to share what fixing it would involve. Worth a short reply?\n\n{sig}")
    B = (f"An opportunity for {l['company']}", f"{hi}\n\n{obs}\n\nThat looks like room to grow without adding headcount. "
         f"We work founder-led, scoped up front, no guaranteed outcomes. If useful, I can outline the options, "
         f"including a light monthly arrangement ({pk}) once the first fix is done.\n\n{sig}")
    C = (f"Mini-audit for {l['website']}?", f"{hi}\n\n{obs}\n\nI can send a short, free mini-audit of the specific areas "
         f"I found, with no obligation. Want me to send it over?\n\n{sig}")
    fus = ["Following up on my note about {c}. Happy to send the details if helpful.",
           "One more idea relating to {c}'s site: {p}. Open to a quick look?",
           "Last note from me. If this isn't a priority for {c}, no problem at all."]
    out = [("A",0)+A,("B",0)+B,("C",0)+C]
    for i,(d,t) in enumerate(zip(FOLLOWUP_DAYS[1:], fus), 1):
        out.append(("A",i,f"Re: {A[0]}", f"{hi}\n\n{t.format(c=l['company'],p=l['problem'])}\n\n{sig}"))
    return out

def save_drafts(lead_id):
    with conn() as c:
        l = dict(c.execute("select * from leads where id=?", (lead_id,)).fetchone())
        c.execute("delete from emails where lead_id=? and approved=0", (lead_id,))
        for ap, step, sub, body in draft(l):
            c.execute("insert into emails(lead_id,approach,step,subject,body) values(?,?,?,?,?)", (lead_id,ap,step,sub,body))
        c.execute("update leads set status='EMAIL_DRAFTED' where id=? and status in ('NEW','RESEARCHED','QUALIFIED')", (lead_id,))

# ---- compliance gate
def can_send(lead_id, address_set=False):
    with conn() as c:
        l = c.execute("select * from leads where id=?", (lead_id,)).fetchone()
        sup = c.execute("select 1 from suppression where email=?", ((l["contact_email"] or "").lower(),)).fetchone()
    errs = []
    if not l["approved"]: errs.append("lead not human-approved")
    if not l["contact_email"]: errs.append("no contact email")
    if sup or l["status"] in ("OPTED_OUT","REPLIED"): errs.append("suppressed/opted out/already replied")
    if not address_set: errs.append("physical address not configured (CAN-SPAM)")
    if l["email_status"] == "inferred": errs.append("inferred email; verify first")
    return errs

def opt_out(email, reason="opt-out"):
    with conn() as c:
        c.execute("insert or replace into suppression values(?,?,?)", (email.lower(), reason, now()))
        c.execute("update leads set status='OPTED_OUT' where lower(contact_email)=?", (email.lower(),))

def _safe(v):  # neutralise spreadsheet formula injection
    return "'"+v if isinstance(v,str) and v and v[0] in "=+-@\t\r" else v
def export_csv(where="1=1", args=()):
    with conn() as c: rows = c.execute(f"select * from leads where {where} order by score desc", args).fetchall()
    if not rows: return ""
    o = io.StringIO(); w = csv.writer(o); w.writerow(rows[0].keys()); w.writerows([[_safe(v) for v in r] for r in rows]); return o.getvalue()

def import_csv(path):
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f): upsert({k:v for k,v in r.items() if v != ""}); n += 1
    return n

# ---- dashboard (localhost only)
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self, body, ct="text/html", code=200):
        self.send_response(code); self.send_header("Content-Type", ct+"; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == "/export.csv": return self._send(export_csv(), "text/csv")
        sql, args = "select * from leads where 1=1", []
        for k in ("country","service","status"):
            if q.get(k,[""])[0]: sql += f" and {k}=?"; args.append(q[k][0])
        if q.get("min",[""])[0]: sql += " and score>=?"; args.append(int(q["min"][0]))
        with conn() as c: rows = c.execute(sql+" order by score desc", args).fetchall()
        esc = lambda s: (str(s) if s is not None else "").replace("&","&amp;").replace("<","&lt;")
        tr = "".join(f"<tr><td>{r['score']}</td><td><a href='{esc(r['website'])}'>{esc(r['company'])}</a><br><small>{esc(r['country'])} {esc(r['city'])}</small></td>"
            f"<td>{esc(r['service'])}</td><td>{esc(r['problem'])}<br><small><a href='{esc(r['evidence_url'])}'>source</a> · {esc(r['researched_at'])}</small></td>"
            f"<td>{esc(r['contact_name'])} {esc(r['contact_email'])}<br><small>{esc(r['email_status'])}</small></td>"
            f"<td>{esc(r['status'])}<br><form method=post action=/act><input type=hidden name=id value={r['id']}>"
            f"<button name=a value=approve>Approve</button><button name=a value=reject>Reject</button><button name=a value=draft>Draft</button></form></td>"
            f"<td>${r['est_mrr_low'] or 0}-{r['est_mrr_high'] or 0}/mo</td></tr>" for r in rows)
        self._send(f"""<!doctype html><meta name=viewport content='width=device-width'><title>Softora Leads</title>
<style>body{{font:14px system-ui;margin:2rem;color:#0b1b3a}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.5rem;vertical-align:top;text-align:left}}button{{margin:2px}}</style>
<h1>Softora Lead Pipeline</h1><form>Country <input name=country size=3> Service <input name=service> Status <input name=status> Min score <input name=min size=3>
<button>Filter</button> <a href=/export.csv>Export CSV</a></form><p>{len(rows)} leads. Drafts only; nothing is sent automatically.</p>
<table><tr><th>Score</th><th>Company</th><th>Service</th><th>Problem + evidence</th><th>Contact</th><th>Status/Actions</th><th>Est. MRR</th></tr>{tr}</table>""")
    def do_POST(self):
        d = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length",0))).decode())
        i, a = int(d["id"][0]), d["a"][0]
        if a == "draft":
            try: save_drafts(i)
            except ValueError: pass
        else:
            with conn() as c:
                c.execute("update leads set approved=?, status=? where id=?", (1,"APPROVED",i) if a=="approve" else (0,"LOST",i))
        self.send_response(303); self.send_header("Location","/"); self.end_headers()

if __name__ == "__main__":
    init(); cmd = sys.argv[1] if len(sys.argv)>1 else "serve"
    if cmd == "import": print(import_csv(sys.argv[2]), "leads imported")
    elif cmd == "export": print(export_csv())
    elif cmd == "doctor": 
        import agent; agent.doctor()
        # Also check lead finder modules
        try:
            from lead_finder import __init__ as lf_init
            from lead_finder.database import init_db
            from lead_finder.processor import LeadProcessor
            from lead_finder.bulk_processor import BulkProcessor, bulk_engine
            print("✓ Lead Finder modules loaded successfully")
            print("✓ Database initialized")
            print("✓ Lead Processor ready")
            print("✓ Bulk Processor ready")
        except Exception as e:
            print(f"✗ Lead Finder error: {e}")
    elif cmd == "bulk":
        # Bulk processing commands
        from lead_finder.bulk_processor import run_bulk_discovery, run_bulk_enrichment, run_bulk_scoring, run_full_pipeline
        if len(sys.argv) > 2:
            subcmd = sys.argv[2]
            if subcmd == "discover":
                import argparse
                parser = argparse.ArgumentParser()
                parser.add_argument("--keywords", nargs="*", default=None)
                parser.add_argument("--limit", type=int, default=50)
                parser.add_argument("--no-enrich", action="store_true")
                parser.add_argument("--deep", action="store_true")
                args = parser.parse_args(sys.argv[3:])
                run_bulk_discovery(
                    keywords=args.keywords,
                    limit_per_source=args.limit,
                    enrich=not args.no_enrich,
                    deep_enrich=args.deep
                )
            elif subcmd == "enrich":
                from lead_finder.bulk_processor import run_bulk_enrichment
                import argparse
                parser = argparse.ArgumentParser()
                parser.add_argument("--days", type=int, default=7)
                parser.add_argument("--limit", type=int, default=100)
                parser.add_argument("--deep", action="store_true")
                args = parser.parse_args(sys.argv[3:])
                run_bulk_enrichment(
                    days=args.days,
                    limit=args.limit,
                    deep=args.deep
                )
            elif subcmd == "score":
                import argparse
                parser = argparse.ArgumentParser()
                parser.add_argument("--limit", type=int, default=100)
                args = parser.parse_args(sys.argv[3:])
                run_bulk_scoring(limit=args.limit)
            elif subcmd == "pipeline":
                import argparse
                parser = argparse.ArgumentParser()
                parser.add_argument("--keywords", nargs="*", default=None)
                parser.add_argument("--limit", type=int, default=50)
                parser.add_argument("--no-enrich", action="store_true")
                parser.add_argument("--deep", action="store_true")
                args = parser.parse_args(sys.argv[3:])
                run_full_pipeline(
                    keywords=args.keywords,
                    limit_per_source=args.limit,
                    enrich=not args.no_enrich,
                    deep_enrich=args.deep
                )
            else:
                print("Usage: python app.py bulk [discover|enrich|score|pipeline]")
        else:
            print("Usage: python app.py bulk [discover|enrich|score|pipeline]")
    elif cmd == "scrape":
        # Advanced scraping commands
        if len(sys.argv) > 2:
            subcmd = sys.argv[2]
            if subcmd == "search":
                from lead_finder.search import search, paginated_search, multi_engine_search, discover_leads
                import argparse
                parser = argparse.ArgumentParser(description="Advanced search engine scraping")
                parser.add_argument("--query", type=str, required=True, help="Search query")
                parser.add_argument("--engine", type=str, default="google", help="Search engine: google, bing, yahoo, duckduckgo")
                parser.add_argument("--limit", type=int, default=20, help="Number of results")
                parser.add_argument("--pages", type=int, default=1, help="Number of pages to scrape")
                parser.add_argument("--output", type=str, default=None, help="Output file (JSON or CSV)")
                parser.add_argument("--keywords", nargs="*", default=None, help="Keywords for B2B lead discovery")
                parser.add_argument("--industries", nargs="*", default=None, help="Industries to target")
                parser.add_argument("--locations", nargs="*", default=None, help="Locations to target")
                args = parser.parse_args(sys.argv[3:])
                
                if args.keywords:
                    # B2B lead discovery
                    leads = discover_leads(
                        keywords=args.keywords,
                        industries=args.industries,
                        locations=args.locations,
                        limit=args.limit
                    )
                    print(f"Found {len(leads)} potential leads")
                    if args.output:
                        import json
                        with open(args.output, 'w') as f:
                            json.dump(leads, f, indent=2)
                        print(f"Saved to {args.output}")
                else:
                    # Regular search
                    results = paginated_search(
                        args.query,
                        engine=args.engine,
                        total_results=args.limit * args.pages
                    )
                    print(f"Found {len(results)} results for '{args.query}'")
                    
                    if args.output:
                        import json
                        with open(args.output, 'w') as f:
                            json.dump(results, f, indent=2)
                        print(f"Saved to {args.output}")
                    else:
                        for i, result in enumerate(results[:10], 1):
                            print(f"{i}. {result.get('title', 'No title')}")
                            print(f"   {result.get('url', 'No URL')}")
                            print()
            elif subcmd == "javascript":
                from lead_finder.javascript import render_page, js_renderer, extract_spa_data
                import argparse
                parser = argparse.ArgumentParser(description="JavaScript rendering for SPAs")
                parser.add_argument("url", type=str, help="URL to render")
                parser.add_argument("--wait", type=float, default=5.0, help="Wait time for JS (seconds)")
                parser.add_argument("--output", type=str, default=None, help="Output file")
                parser.add_argument("--extract", action="store_true", help="Extract SPA data")
                args = parser.parse_args(sys.argv[3:])
                
                html, metadata = render_page(args.url, timeout=args.wait)
                print(f"Rendered: {args.url}")
                print(f"Has dynamic content: {metadata.get('has_dynamic_content')}")
                print(f"Scripts loaded: {metadata.get('scripts_loaded')}")
                print(f"XHR requests: {metadata.get('xhr_requests')}")
                
                if args.extract:
                    spa_data = extract_spa_data(html)
                    print(f"\nSPA Data:")
                    for framework, data in spa_data.items():
                        print(f"  {framework}: {len(data) if isinstance(data, list) else '1 object'}")
                
                if args.output:
                    with open(args.output, 'w') as f:
                        f.write(html)
                    print(f"Saved to {args.output}")
            elif subcmd in ("captcha", "stealth"):
                print("Removed: Dealwright crawls politely (honest user agent, rate limits, robots.txt) and skips sites that present CAPTCHAs.")
            else:
                print("Usage: python app.py scrape [search|javascript]")
        else:
            print("Usage: python app.py scrape [search|javascript]")
    else:
        import crm
        args = ([cmd] if cmd.isdigit() else []) + sys.argv[2:]
        if args and args[0] in ("-h", "--help"):
            raise SystemExit("Usage: python app.py serve [PORT]   (default 8000, or set SOFTORA_PORT)")
        port = None
        if args:
            try: port = int(args[0])
            except ValueError: raise SystemExit(f"Invalid port: {args[0]}")
            if not 0 < port < 65536: raise SystemExit(f"Port out of range: {port}")
        crm.serve(port=port)
