"""oradev growth desk.

Drafts, scores, and the daily queue live here. This module never sends email,
never posts, and never messages WhatsApp. Opt-outs are stored and excluded
from the queue and from draft generation.
"""
import os
import re
from datetime import date, timedelta

from app import conn, now

STATUSES = (
    "new", "briefed", "sent", "connected", "replied",
    "shortlist", "demo", "trial", "won", "lost", "opted_out", "closed_no_fit",
)
REPLY_CLASSES = ("", "interested", "objection", "not_now", "opt_out")
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com",
    "outlook.com", "live.com", "icloud.com",
}
ICP_INDUSTRY = (
    "software", "it services", "information technology", "agency", "bpo",
    "staffing", "startup", "e-commerce", "ecommerce", "fintech", "digital",
)
CITIES = (
    "karachi", "lahore", "islamabad", "rawalpindi",
    "dubai", "abu dhabi", "riyadh", "doha",
)
TOUCH_GAP = {1: 3, 2: 3, 3: 4, 4: 4, 5: 0}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

POSTS = [
    {"id": 1, "pillar": "Hiring mistakes", "hook": "A role open 30 days is not a pipeline. It’s a delay.",
     "body": "The people you wanted often have another offer by then. Pull every req older than 30 days and rewrite the must-haves as three lines."},
    {"id": 2, "pillar": "Hiring mistakes", "hook": "Stop asking for 14 skills on a Flutter role.",
     "body": "Pick three. Screen for those. Interview the rest."},
    {"id": 3, "pillar": "AI boundary", "hook": "AI should rank. It should not reject.",
     "body": "If you can’t explain the rank, don’t use it."},
    {"id": 4, "pillar": "Hiring clock", "hook": "Your careers page says you’re hiring. The post is from March.",
     "body": "Either the role is real or the page is stale. Update it."},
    {"id": 5, "pillar": "Hiring mistakes", "hook": "Founders don’t have a sourcing problem. They have a reading problem.",
     "body": "Two hundred CVs in a spare hour is how bad offers get made. Eight ranked profiles is a shortlist."},
    {"id": 6, "pillar": "Hiring clock", "hook": "Screen the CVs you already have before you pay an agency for more.",
     "body": "The fee makes sense after you know the pile is actually thin."},
    {"id": 7, "pillar": "Hiring mistakes", "hook": "No-shows are a process bug.",
     "body": "Confirm the morning of the interview, on the channel they answer."},
    {"id": 8, "pillar": "Behind oradev", "hook": "Don’t buy a hiring tool to feel modern.",
     "body": "Buy one hour back on a live req, or don’t buy."},
    {"id": 9, "pillar": "AI boundary", "hook": "A shortlist of 40 is still a pile.",
     "body": "If the hiring manager won’t read it tonight, it isn’t short."},
    {"id": 10, "pillar": "Behind oradev", "hook": "We won’t quote a speed number until five companies let us time their process.",
     "body": "Until then the offer is one role, ranked in 24 hours."},
]


def ensure():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS growth_leads(
          id INTEGER PRIMARY KEY,
          date_found TEXT, source_url TEXT, company TEXT NOT NULL, website TEXT,
          city TEXT, size TEXT, industry TEXT, signal TEXT, signal_age INT DEFAULT 0,
          open_roles INT DEFAULT 1, also_on_site INT DEFAULT 0,
          large_ta_team INT DEFAULT 0, hidden_client INT DEFAULT 0,
          buyer_name TEXT, buyer_title TEXT, channel TEXT, email TEXT,
          score INT DEFAULT 0, score_reason TEXT, band TEXT,
          pain_hypothesis TEXT, proof_used TEXT DEFAULT 'none',
          status TEXT DEFAULT 'new', next_action TEXT, next_date TEXT,
          variant TEXT, touch INT DEFAULT 0, reply_class TEXT DEFAULT '',
          opt_out INT DEFAULT 0, opt_out_at TEXT,
          created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS growth_messages(
          id INTEGER PRIMARY KEY, lead_id INT, channel TEXT, step INT,
          subject TEXT, body TEXT, status TEXT DEFAULT 'draft', created_at TEXT);
        CREATE TABLE IF NOT EXISTS growth_events(
          id INTEGER PRIMARY KEY, lead_id INT, kind TEXT, detail TEXT, at TEXT);
        """)


def profile():
    name = os.environ.get("GROWTH_SENDER_NAME", "").strip()
    email = os.environ.get("GROWTH_SENDER_EMAIL", "").strip()
    address = (os.environ.get("GROWTH_POSTAL_ADDRESS") or os.environ.get("SOFTORASYSTEMS_ADDRESS") or "").strip()
    ready = bool(name and email and address and EMAIL_RE.match(email) and _domain(email) not in PERSONAL_DOMAINS)
    return {
        "sender_name": name or "[Your Name]",
        "sender_email": email or "[you@yourdomain]",
        "postal_address": address or "[postal address]",
        "ready_to_send": ready,
        "sends_mail": False,
    }


def _domain(email):
    if "@" not in (email or ""):
        return ""
    return email.split("@", 1)[1].lower().strip()


def _size_num(size):
    m = re.search(r"\d+", str(size or ""))
    return int(m.group()) if m else None


def _flag(v):
    return 1 if v in (True, 1, "1", "true", "yes", "on") else 0


def score(row):
    """0–100 from the public hiring signal. No live role scores 0."""
    reasons = []
    signal = (row.get("signal") or "").strip()
    if not signal:
        return 0, "drop", "No live role. Do not contact."
    points = 0
    age = int(row.get("signal_age") or 0)
    if age >= 30:
        points += 20
        reasons.append("+20 role open 30+ days")
    elif age >= 14:
        points += 10
        reasons.append("+10 role open 14+ days")
    if int(row.get("open_roles") or 1) >= 2:
        points += 10
        reasons.append("+10 two or more open roles")
    if _flag(row.get("also_on_site")):
        points += 10
        reasons.append("+10 on a board and their own site")
    n = _size_num(row.get("size"))
    if n is not None and 10 <= n <= 200:
        points += 15
        reasons.append("+15 company size 10–200")
    industry = (row.get("industry") or "").lower()
    if any(k in industry for k in ICP_INDUSTRY):
        points += 10
        reasons.append("+10 ICP industry")
    city = (row.get("city") or "").lower()
    if any(k in city for k in CITIES):
        points += 10
        reasons.append("+10 launch-market city")
    if (row.get("buyer_name") or "").strip() and (row.get("buyer_title") or "").strip():
        points += 15
        reasons.append("+15 named buyer with a title")
    elif (row.get("buyer_name") or "").strip():
        points += 8
        reasons.append("+8 buyer name only")
    email = (row.get("email") or "").strip().lower()
    if email and EMAIL_RE.match(email) and _domain(email) not in PERSONAL_DOMAINS:
        points += 10
        reasons.append("+10 public business email")
    elif (row.get("channel") or "").lower() in ("form", "contact form"):
        points += 5
        reasons.append("+5 contact form only")
    if email and _domain(email) in PERSONAL_DOMAINS:
        reasons.append("personal inbox adds no points and blocks drafts")
    if _flag(row.get("large_ta_team")):
        points -= 15
        reasons.append("−15 in-house recruiting team of 5+")
    if _flag(row.get("hidden_client")):
        points -= 10
        reasons.append("−10 staffing post for a hidden client")
    points = max(0, min(100, points))
    band = "contact" if points >= 70 else "hold" if points >= 50 else "drop"
    return points, band, "; ".join(reasons)


def _clean_email(email):
    email = (email or "").strip().lower()
    if not email:
        return ""
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ValueError("invalid email")
    return email


def _words(text):
    return len(re.findall(r"\S+", text or ""))


def brief(row):
    p = profile()
    company = row.get("company") or "This company"
    what = row.get("industry") or "their market"
    signal = row.get("signal") or "a role"
    age = int(row.get("signal_age") or 0)
    source = row.get("source_url") or "a public page"
    pain = (row.get("pain_hypothesis") or "").strip() or (
        f"Someone is still reading every CV for the {signal} search."
    )
    line = (
        f"Your {signal} role has been open {age} days. "
        f"I can run that req and send a ranked shortlist within 24 hours."
    )
    text = (
        f"{company} works in {what}, based in {row.get('city') or 'an unknown city'}. "
        f"Signal: {signal}, {age} days, source {source}. "
        f"Pain: {pain} "
        f"Line: “{line}” "
        f"Feature: the shortlist, so they read about eight people instead of the full pile. "
        f"Proof used: {row.get('proof_used') or 'none'}. "
        f"Score {row.get('score')} ({row.get('band')}). "
        f"Next: {row.get('next_action') or 'review'}."
    )
    return {"text": text, "opening_line": line, "words": _words(text), "sender_ready": p["ready_to_send"]}


def _fill(template, row):
    p = profile()
    age = int(row.get("signal_age") or 0)
    data = {
        "Name": (row.get("buyer_name") or "there").split()[0],
        "Company": row.get("company") or "your company",
        "Role": row.get("signal") or "open role",
        "Days": str(age),
        "Your Name": p["sender_name"],
        "Postal address": p["postal_address"],
    }
    out = template
    for k, v in data.items():
        out = out.replace(f"[{k}]", v)
    return out


def _sequence(row):
    emails = [
        (1, [
            "[Role] at [Company] — 24h shortlist",
            "Your [Role] req, still open",
            "[Name], [Days] days on the [Role] search",
        ], "[Name] — the [Role] role at [Company] has been open [Days] days. I run oradev.app. We take one live req and return an AI-ranked shortlist in 24–48 hours, free for the first job. Worth doing that for this [Role]?\n[Your Name], oradev.app\nReply “stop” and I won’t email again.\n[Postal address]"),
        (2, [
            "Still happy to run the [Role]",
            "One req, then you decide",
            "[Company] [Role] shortlist",
        ], "I can only do a useful shortlist from the real req. If the [Role] is still open, reply with the must-haves and I’ll send ranked candidates in 24–48 hours. If it’s filled, say so and I’ll close it.\nReply “stop” to opt out.\n[Your Name]\n[Postal address]"),
        (3, [
            "What the shortlist includes",
            "8 names, not 200 CVs",
            "For the [Role] search",
        ], "One role. People ranked against your must-haves, with the reason each name is up or down. Nothing is auto-rejected. You make the call. Should I run the [Role]?\nReply “stop” to opt out.\n[Your Name]\n[Postal address]"),
        (4, [
            "Different pain?",
            "Is screening even the bottleneck?",
            "One question on the [Role]",
        ], "If the delay is interviews, no-shows, or the hiring manager’s calendar, say which. I’ll tell you if oradev helps that. If you want the shortlist anyway, say go.\nReply “stop” to opt out.\n[Your Name]\n[Postal address]"),
        (5, [
            "Closing the loop on [Role]",
            "Last note from me",
            "I’ll assume the req is paused",
        ], "Last note on the [Role] at [Company]. I’ll assume the search is paused. If it reopens, reply on this thread.\nReply “stop” if you want that recorded.\n[Your Name], oradev.app\n[Postal address]"),
    ]
    out = []
    for step, subjects, body in emails:
        filled = [_fill(s, row) for s in subjects]
        text = _fill(body, row)
        out.append({
            "channel": "email", "step": step, "subjects": filled,
            "subject": filled[0], "body": text, "words": _words(text),
        })
    note = _fill("[Name] — saw the [Role] role at [Company] ([Days] days). I help teams turn one open req into a ranked shortlist in 24h. Fine to connect?", row)
    follows = [
        _fill("Thanks for connecting. I can run the [Role] through oradev and send a ranked shortlist in 24–48 hours, free for one job. Want that?", row),
        _fill("If the [Role] is filled, ignore this. If it’s open, send three must-haves and I’ll do the shortlist. You choose who moves on.", row),
        _fill("I’ll leave it here. If that [Role] stalls again, one free shortlist is still available.", row),
    ]
    out.append({"channel": "linkedin", "step": 0, "subject": "Connection note", "body": note, "chars": len(note), "words": _words(note)})
    for i, text in enumerate(follows, start=1):
        out.append({"channel": "linkedin", "step": i, "subject": f"Follow-up {i}", "body": text, "words": _words(text)})
    wa = _fill("Hi [Name], it’s [Your Name] from oradev. You asked me to look at the [Role]. I’ll send the shortlist by the time we agreed. Reply STOP to end these messages.", row)
    out.append({
        "channel": "whatsapp", "step": 1, "subject": "Opted-in only",
        "body": wa, "words": _words(wa),
        "gate": "Send only after they ask or put a number on your form.",
    })
    return out


def _row_from_body(b, existing=None):
    base = dict(existing or {})
    keys = (
        "source_url", "company", "website", "city", "size", "industry", "signal",
        "buyer_name", "buyer_title", "channel", "email", "pain_hypothesis", "proof_used",
        "variant", "next_action", "next_date", "status", "reply_class",
    )
    for k in keys:
        if k in b and b[k] is not None:
            base[k] = str(b[k]).strip()
    for k in ("signal_age", "open_roles"):
        if k in b and b[k] not in (None, ""):
            base[k] = int(b[k])
    for k in ("also_on_site", "large_ta_team", "hidden_client"):
        if k in b:
            base[k] = _flag(b[k])
    if "email" in b:
        base["email"] = _clean_email(b.get("email"))
    company = (base.get("company") or "").strip()
    if not company or len(company) > 200:
        raise ValueError("company is required")
    if base.get("status") and base["status"] not in STATUSES:
        raise ValueError("invalid status")
    if base.get("reply_class") and base["reply_class"] not in REPLY_CLASSES:
        raise ValueError("invalid reply class")
    if base.get("next_date"):
        date.fromisoformat(base["next_date"])
    points, band, reason = score(base)
    base["score"] = points
    base["band"] = band
    base["score_reason"] = reason
    if not base.get("date_found"):
        base["date_found"] = now()[:10]
    if not base.get("status"):
        base["status"] = "briefed" if band == "contact" else "new"
    if not base.get("next_action"):
        base["next_action"] = {
            "contact": "Send email 1 today",
            "hold": "Hold for a slow day",
            "drop": "Do not contact",
        }[band]
    if band == "contact" and not base.get("next_date"):
        base["next_date"] = date.today().isoformat()
    if not base.get("proof_used"):
        base["proof_used"] = "none"
    return base


def _insert_event(c, lead_id, kind, detail):
    c.execute(
        "insert into growth_events(lead_id,kind,detail,at) values(?,?,?,?)",
        (lead_id, kind, str(detail)[:500], now()),
    )


def create_lead(body):
    ensure()
    row = _row_from_body(body)
    cols = (
        "date_found", "source_url", "company", "website", "city", "size", "industry",
        "signal", "signal_age", "open_roles", "also_on_site", "large_ta_team", "hidden_client",
        "buyer_name", "buyer_title", "channel", "email", "score", "score_reason", "band",
        "pain_hypothesis", "proof_used", "status", "next_action", "next_date", "variant",
    )
    numeric = {"signal_age", "open_roles", "also_on_site", "large_ta_team", "hidden_client", "score"}
    row.setdefault("signal_age", 0)
    row.setdefault("open_roles", 1)
    values = [row.get(c) if row.get(c) is not None else (0 if c in numeric else "") for c in cols]
    with conn() as c:
        c.execute(
            f"insert into growth_leads({','.join(cols)},opt_out,created_at,updated_at) values({','.join('?'*len(cols))},0,?,?)",
            values + [now(), now()],
        )
        lid = c.execute("select last_insert_rowid()").fetchone()[0]
        _insert_event(c, lid, "created", f"score {row['score']} {row['band']}")
    return get_lead(lid)


def update_lead(lid, body):
    ensure()
    current = get_lead(lid)
    if current["opt_out"] and body.get("status") not in (None, "opted_out"):
        raise ValueError("opted out; this company is not contacted again")
    if body.get("reply_class") == "opt_out" or body.get("status") == "opted_out":
        return opt_out(lid)
    merged = _row_from_body(body, current)
    sets = (
        "source_url", "company", "website", "city", "size", "industry", "signal",
        "signal_age", "open_roles", "also_on_site", "large_ta_team", "hidden_client",
        "buyer_name", "buyer_title", "channel", "email", "score", "score_reason", "band",
        "pain_hypothesis", "proof_used", "status", "next_action", "next_date", "variant", "reply_class",
    )
    with conn() as c:
        c.execute(
            f"update growth_leads set {','.join(k+'=?' for k in sets)}, updated_at=? where id=?",
            [merged.get(k) for k in sets] + [now(), lid],
        )
        _insert_event(c, lid, "updated", merged.get("status") or "")
    return get_lead(lid)


def opt_out(lid):
    ensure()
    with conn() as c:
        if not c.execute("select 1 from growth_leads where id=?", (lid,)).fetchone():
            raise LookupError("growth lead not found")
        c.execute(
            "update growth_leads set opt_out=1, opt_out_at=?, status='opted_out', next_action='Do not contact', updated_at=? where id=?",
            (now(), now(), lid),
        )
        c.execute("update growth_messages set status='cancelled' where lead_id=? and status='draft'", (lid,))
        _insert_event(c, lid, "opt_out", "excluded from queue and drafts")
    return get_lead(lid)


def get_lead(lid):
    ensure()
    with conn() as c:
        r = c.execute("select * from growth_leads where id=?", (lid,)).fetchone()
        if not r:
            raise LookupError("growth lead not found")
        lead = dict(r)
        msgs = [dict(m) for m in c.execute(
            "select * from growth_messages where lead_id=? order by channel, step, id", (lid,)
        )]
        events = [dict(e) for e in c.execute(
            "select kind, detail, at from growth_events where lead_id=? order by id desc limit 20", (lid,)
        )]
    lead["brief"] = brief(lead)
    lead["messages"] = msgs
    lead["events"] = events
    return lead


def list_leads():
    ensure()
    with conn() as c:
        rows = [dict(r) for r in c.execute("select * from growth_leads order by score desc, id desc limit 200")]
    return rows


def daily_queue():
    ensure()
    today = date.today().isoformat()
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            """select * from growth_leads
               where opt_out=0 and band='contact' and score>=70
                 and status not in ('won','lost','opted_out','closed_no_fit','drop')
                 and (next_date is null or next_date='' or next_date<=?)
               order by score desc, id asc limit 8""",
            (today,),
        )]
    return rows


def generate_drafts(lid):
    ensure()
    lead = get_lead(lid)
    if lead["opt_out"]:
        raise ValueError("opted out; drafts are not created")
    if lead["band"] == "drop":
        raise ValueError("score is in the drop band; do not draft outreach")
    email = (lead.get("email") or "").lower()
    if email and _domain(email) in PERSONAL_DOMAINS:
        raise ValueError("personal inbox; use a business email before drafting")
    seq = _sequence(lead)
    with conn() as c:
        c.execute("delete from growth_messages where lead_id=? and status='draft'", (lid,))
        for item in seq:
            c.execute(
                "insert into growth_messages(lead_id,channel,step,subject,body,status,created_at) values(?,?,?,?,?,'draft',?)",
                (lid, item["channel"], item["step"], item.get("subject") or "", item["body"], now()),
            )
        _insert_event(c, lid, "drafts", f"{len(seq)} drafts stored, nothing sent")
    stored = get_lead(lid)
    stored["sequence"] = seq
    return stored


def mark_touch(lid, step, variant="A"):
    """Record that the user sent a touch themselves. This does not send mail."""
    ensure()
    lead = get_lead(lid)
    if lead["opt_out"]:
        raise ValueError("opted out")
    step = int(step)
    if step < 1 or step > 5:
        raise ValueError("step must be 1–5")
    variant = (variant or "A").strip().upper()[:1]
    if variant not in ("A", "B", "C"):
        raise ValueError("variant must be A, B, or C")
    gap = TOUCH_GAP[step]
    nxt = (date.today() + timedelta(days=gap)).isoformat() if gap else ""
    action = "Close the loop" if step == 5 else f"Send email {step + 1}"
    status = "sent" if lead["status"] in ("new", "briefed", "sent") else lead["status"]
    with conn() as c:
        c.execute(
            """update growth_leads set touch=?, variant=?, status=?, next_action=?, next_date=?, updated_at=?
               where id=?""",
            (step, variant, status, action, nxt, now(), lid),
        )
        c.execute(
            "update growth_messages set status='logged' where lead_id=? and channel='email' and step=?",
            (lid, step),
        )
        _insert_event(c, lid, "touch_logged", f"email {step} variant {variant}; not sent by the app")
    return get_lead(lid)


def metrics():
    ensure()
    with conn() as c:
        rows = [dict(r) for r in c.execute("select status, score, band, opt_out, touch, reply_class from growth_leads")]
    def n(pred):
        return sum(1 for r in rows if pred(r))
    contacted_status = {"sent", "connected", "replied", "shortlist", "demo", "trial", "won"}
    found = len(rows)
    high = n(lambda r: r["score"] >= 70 and not r["opt_out"])
    contacted = n(lambda r: (r["touch"] or 0) >= 1 or r["status"] in contacted_status)
    replied = n(lambda r: (r["reply_class"] or "") != "")
    qualified = n(lambda r: r["reply_class"] == "interested" or r["status"] in ("shortlist", "demo", "trial", "won"))
    demo = n(lambda r: r["status"] in ("demo", "trial", "won"))
    trial = n(lambda r: r["status"] in ("trial", "won"))
    paid = n(lambda r: r["status"] == "won")
    opt_outs = n(lambda r: r["opt_out"])
    stages = [
        ("found", found), ("scored_70", high), ("contacted", contacted), ("replied", replied),
        ("qualified", qualified), ("demo", demo), ("trial", trial), ("paid", paid),
    ]
    leak = ""
    if contacted >= 30 and replied / contacted < 0.05:
        leak = "Reply rate is under 5% after 30 sends. Fix the list and the first line before adding volume."
    elif replied >= 5 and qualified / max(replied, 1) < 0.30:
        leak = "Polite replies are being counted. Qualified means interest in the named role."
    elif qualified >= 3 and demo / max(qualified, 1) < 0.5 and n(lambda r: r["status"] == "shortlist") >= 2:
        leak = "Shortlists are not turning into demos. Check that the list used their must-haves."
    return {
        "stages": [{"name": a, "count": b} for a, b in stages],
        "opt_outs": opt_outs,
        "leak": leak,
        "targets": {"week1_touches_per_day": 12, "week4_touches_per_day": 36, "qualified_per_day_day30": 3},
    }


def desk():
    return {
        "profile": profile(),
        "metrics": metrics(),
        "today": daily_queue(),
        "items": list_leads(),
        "posts": POSTS,
    }
