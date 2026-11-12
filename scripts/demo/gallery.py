"""Generate the hackathon image gallery: real screenshots of the running app,
framed at 1800x1200 (3:2) with a headline, plus a cover and an architecture card.

    python scripts/demo/gallery.py            # after scripts/demo/record.sh has run

Serves a *copy* of the recording's demo database on its own port, so the
screens show the real PayPal sandbox payment and the agents' real output.
Writes demo/gallery/NN-name.png (each well under 5 MB).
"""
import base64, html, os, shutil, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

PORT, TOKEN = 8795, "gallery-token-0000000000000"
os.environ.setdefault("DEMO_CDP_PORT", "9455")  # never collide with a recording in progress
os.environ["SOFTORASYSTEMS_TOKEN"] = TOKEN
os.environ["SANDBOX_DISABLE_DOCKER"] = "1"
os.environ["OPENROUTER_API_KEY"] = ""  # read-only screenshots: no LLM or PayPal calls
os.environ["PAYPAL_CLIENT_ID"] = os.environ.get("PAYPAL_CLIENT_ID") or "unused"

SRC_DB = os.path.join(ROOT, "scripts", "demo", ".demo", "demo.db")
OUT = os.path.join(ROOT, "demo", "gallery")
if not os.path.exists(SRC_DB): raise SystemExit("Run scripts/demo/record.sh first (it creates the demo database).")
work = tempfile.mkdtemp(prefix="gallery_")
import sqlite3
with sqlite3.connect(SRC_DB) as src, sqlite3.connect(os.path.join(work, "demo.db")) as dst:
    src.backup(dst)  # consistent snapshot, including writes still in the WAL

import app
app.DB = os.path.join(work, "demo.db")
import crm, paypal, paypal_web, ops
crm.migrate()
srv = crm.make_server("127.0.0.1", PORT)
threading.Thread(target=srv.serve_forever, daemon=True).start()
import recorder as dr

FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}body{{width:1800px;height:1200px;overflow:hidden;font-family:Inter,'DejaVu Sans',system-ui,sans-serif;
background:radial-gradient(circle at 15% 0%,#1e3a8a 0%,#0b1020 50%,#05070f 100%);color:#fff;padding:54px 80px}}
.k{{font:700 20px/1 inherit;letter-spacing:.14em;text-transform:uppercase;color:#60a5fa}}
h1{{font-size:54px;letter-spacing:-1.2px;margin:14px 0 8px;line-height:1.08}}p{{font-size:25px;color:#cbd5e1;max-width:1500px}}
.shot{{margin-top:34px;border-radius:18px;overflow:hidden;box-shadow:0 30px 80px #000c,0 0 0 1px #ffffff1f;width:1640px;height:{h}px}}
.shot img{{width:1640px;display:block}}.brand{{position:absolute;right:80px;top:58px;font:700 22px inherit;color:#93c5fd}}
</style></head><body><div class="brand">Dealwright · AI × PayPal</div><div class="k">{kicker}</div><h1>{title}</h1><p>{sub}</p>
<div class="shot"><img src="{img}"></div></body></html>"""

CARD = """<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}body{{width:1800px;height:1200px;overflow:hidden;font-family:Inter,'DejaVu Sans',system-ui,sans-serif;
background:radial-gradient(circle at 20% 10%,#1e3a8a 0%,#0b1020 52%,#05070f 100%);color:#fff;padding:90px 110px}}{css}</style></head>
<body>{body}</body></html>"""


def png(b, path):
    b.js("document.getElementById('__demo_cursor')?.remove(); window.__demoCursor = true")  # stills need no cursor
    open(path, "wb").write(base64.b64decode(b.cmd("Page.captureScreenshot", format="png")["result"]["data"]))


def _frame(b, shot_png, kicker, title, sub, name, crop_h=900):
    tmp = os.path.join(work, name + ".html")
    open(tmp, "w").write(FRAME.format(kicker=html.escape(kicker), title=html.escape(title), sub=html.escape(sub),
                                      img="file://" + shot_png, h=crop_h))
    b.cmd("Emulation.setDeviceMetricsOverride", width=1800, height=1200, deviceScaleFactor=1, mobile=False)
    b.goto("file://" + tmp, 1.0)
    png(b, os.path.join(OUT, name + ".png"))
    b.cmd("Emulation.setDeviceMetricsOverride", width=dr.CSS_W, height=dr.CSS_H, deviceScaleFactor=dr.SCALE, mobile=False)


def _card(b, body, css, name):
    tmp = os.path.join(work, name + ".html")
    open(tmp, "w").write(CARD.format(body=body, css=css))
    b.cmd("Emulation.setDeviceMetricsOverride", width=1800, height=1200, deviceScaleFactor=1, mobile=False)
    b.goto("file://" + tmp, 1.0)
    png(b, os.path.join(OUT, name + ".png"))
    b.cmd("Emulation.setDeviceMetricsOverride", width=dr.CSS_W, height=dr.CSS_H, deviceScaleFactor=dr.SCALE, mobile=False)


PENDING = []  # compose after all app screenshots are taken, so the browser never leaves the app mid-run
def frame(b, *a, **k): PENDING.append((_frame, a, k))
def card(b, *a, **k): PENDING.append((_card, a, k))


def main():
    shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
    b = dr.Browser(work)
    base = f"http://localhost:{PORT}"
    raw = lambda n: os.path.join(work, n + ".png")
    try:
        # 01 cover
        card(b, """<div style="font:700 22px inherit;letter-spacing:.16em;color:#60a5fa">BUILD WHAT'S NEXT WITH PAYPAL AND AI</div>
          <div style="font-size:150px;font-weight:800;letter-spacing:-5px;margin:40px 0 10px">Dealwright</div>
          <div style="font-size:44px;color:#e2e8f0;max-width:1400px;line-height:1.25">Your whole software company, run by people and AI agents, from first lead to paid delivery on PayPal.</div>
          <div style="margin-top:70px;display:flex;gap:16px;flex-wrap:wrap">""" + "".join(
            f"<span style='background:#ffffff14;border:1px solid #ffffff2a;border-radius:999px;padding:14px 26px;font-size:26px'>{t}</span>"
            for t in ["PayPal Orders v2", "Subscriptions v1", "Invoicing v2", "Refunds", "Verified webhooks",
                      "20 departments", "9 department AI agents", "Human approval"]) + "</div>", "", "01-cover")

        b.goto(base + "/"); b.js(f"sessionStorage.setItem('t','{TOKEN}')"); b.goto(base + "/", 2.5)

        # 02 company
        b.click("[data-v=org]"); b.find(None, "Client-to-delivery lifecycle", 15); time.sleep(1); png(b, raw("org"))
        frame(b, raw("org"), "The company", "20 departments, staffed by people and AI agents",
              "Revenue, Delivery, Operations and Product & R&D, with live KPIs: conversion, PayPal collections, MRR and overdue work.", "02-company")

        # 03 lead evidence
        b.click("[data-v=table]"); b.find(None, "FinFlow Payments", 15); b.click(None, "FinFlow Payments")
        b.find(None, "Opportunity / Audit Finding", 15); time.sleep(1); png(b, raw("lead"))
        frame(b, raw("lead"), "Sales agents", "Every lead carries evidence from the prospect's own site",
              "Research, scoring and a decision-maker contact, before anyone writes a word of outreach.", "03-lead")

        # 04 proposal pricing
        b.click(None, "View Full SOW"); b.find(None, "Commercial Investment", 15); b.find(None, "Commercial Investment"); time.sleep(1); png(b, raw("proposal"))
        frame(b, raw("proposal"), "Proposal agent", "A scoped proposal with three pricing tiers",
              "One-time sprint or monthly retainer, each one payable through PayPal with a single click.", "04-proposal")
        b.click("[data-act=close]")

        # 05 client pay page (a not-yet-paid proposal) and 06 confirmation
        with app.conn() as c:
            pid = c.execute("select p.id from proposals p join leads l on l.id=p.lead_id where l.company like 'DataStream%'").fetchone()[0]
        tok = paypal.pay_link(pid, base)["token"]
        b.goto(f"{base}/pay/{tok}", 1.5); b.find(None, "Choose your plan"); time.sleep(0.8); png(b, raw("pay"))
        frame(b, raw("pay"), "Client view", "A PayPal button on every plan",
              "One-time tiers check out with PayPal Orders v2; retainers create a PayPal subscription on the fly.", "05-pay-page")
        with app.conn() as c:
            row = c.execute("select py.*, pr.pay_token from payments py join proposals pr on pr.id=py.proposal_id "
                            "where py.kind='subscription' and py.status='active'").fetchone()
        if row:
            confirm = os.path.join(work, "confirm.html"); open(confirm, "w").write(paypal_web.thanks(row["pay_token"], dict(row)))
            b.goto("file://" + confirm, 1.0); png(b, raw("confirm"))
            frame(b, raw("confirm"), "Paid in the PayPal sandbox", "The subscription is live, and the deal is Won",
                  f"PayPal subscription {row['paypal_id']}: no manual data entry from payment to CRM.", "06-paid")

        # 07 team & projects board, 08 lifecycle deliverables
        b.goto(base + "/", 2.0); b.click("[data-v=ops]"); b.find(None, "Open lifecycle", 15); time.sleep(1); png(b, raw("ops"))
        frame(b, raw("ops"), "Delivery", "The paid deal becomes a planned project",
              "Who is free, who is busy and on what, the project queue, and explainable auto-assignment.", "07-team-projects")
        b.click(None, "Open lifecycle"); b.find(None, "Requirement analysis", 15); time.sleep(1.2)
        b.js("document.querySelector('#drawer').scrollTo(0, 420)"); time.sleep(0.6); png(b, raw("life"))
        frame(b, raw("life"), "Department AI agents", "Nine stages, each owned by a department",
              "Agents write client-specific deliverables (estimate, sprint plan, UX, architecture, tests, runbook), and a human approves each one.", "08-agents-at-work")
        b.click("[data-act=close]")

        # 09 copilot invoice (rendered from the real conversation outcome)
        b.click("[data-v=copilot]"); b.find(None, "Who owes us?", 15); b.click(None, "Who owes us?"); b.find(None, "PayPal cash position", 30); time.sleep(1); png(b, raw("wren"))
        frame(b, raw("wren"), "Finance in plain English", "Wren bills and chases payments, never alone",
              "An LLM picks the PayPal tool; invoices, refunds and cancellations wait for a human click.", "09-copilot")

        # 10 payments ledger
        b.click("[data-v=payments]"); b.find("#paygrid .ag-row", None, 20); time.sleep(1); png(b, raw("ledger"))
        frame(b, raw("ledger"), "PayPal ledger", "Every PayPal payment, in an AG Grid ledger",
              "Collected, MRR, outstanding and refunded, with inline sync, AI reminders and refunds.", "10-ledger")

        # 11 architecture
        stage = lambda t, s, c="#1e293b": (f"<div style='background:{c};border:1px solid #ffffff26;border-radius:16px;padding:20px 22px;"
                                          f"min-width:230px'><div style='font-size:27px;font-weight:700'>{t}</div><div style='font-size:20px;color:#cbd5e1;margin-top:6px'>{s}</div></div>")
        arrow = "<div style='font-size:42px;color:#60a5fa;align-self:center'>→</div>"
        card(b, "<div style='font:700 22px inherit;letter-spacing:.16em;color:#60a5fa'>HOW IT WORKS</div>"
                "<div style='font-size:60px;font-weight:800;letter-spacing:-1.5px;margin:16px 0 50px'>One transaction from payment to plan</div>"
                "<div style='display:flex;gap:16px;flex-wrap:wrap'>"
                + stage("Sales agents", "research · evidence") + arrow + stage("Proposal agent", "SOW · 3 tiers") + arrow
                + stage("PayPal", "Orders · Subscriptions · Invoicing", "#003087") + arrow + stage("Deal Won", "MRR recorded") + "</div>"
                "<div style='display:flex;gap:16px;margin-top:26px;flex-wrap:wrap'>" + arrow
                + stage("PM agent", "plans 9 stages") + arrow + stage("Department agents", "write deliverables") + arrow
                + stage("Human approval", "every deliverable & money move", "#14532d") + "</div>"
                "<div style='margin-top:60px;font-size:24px;color:#cbd5e1;line-height:1.6'>Signature-verified, de-duplicated webhooks · idempotent capture and refund · "
                "throttled single-use pay links · bounded agent concurrency · agents write documents and never move money</div>", "", "11-architecture")
        for fn, a, k in PENDING:
            fn(b, *a, **k)
    finally:
        b.close(); srv.shutdown()
    for f in sorted(os.listdir(OUT)):
        print(f"{f}  {os.path.getsize(os.path.join(OUT, f)) // 1024} KB")


if __name__ == "__main__":
    main()
