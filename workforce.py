"""Shimmer voice workforce.

A conductor routes each utterance to one or more specialist agents.
Every agent reads live CRM data through a named connector. Nothing is emailed
or posted unless the matching connector is enabled and the user asked for it.
"""
import json
import urllib.request
from datetime import date, timedelta

import app
import agent
from app import conn, now

TEAM = [
    ("Amina Shah", "Discovery", "research,scraping,intent", "amina@softora.local", 8, "available"),
    ("Leo Park", "Qualification", "scoring,audit,evidence", "leo@softora.local", 6, "available"),
    ("Nora Iqbal", "Proposals", "sow,pricing,closing", "nora@softora.local", 5, "busy"),
    ("Mateo Ruiz", "Outreach", "email,sequencing,follow-up", "mateo@softora.local", 7, "available"),
    ("Priya Nair", "Sandbox", "security,containment,audit", "priya@softora.local", 4, "available"),
    ("Jonah Ellis", "Closing", "pipeline,meetings,negotiation", "jonah@softora.local", 5, "busy"),
]

CONNECTORS = [
    {"id": "crm", "name": "Pipeline CRM", "kind": "native", "blurb": "Leads, stages, scores, and revenue."},
    {"id": "email", "name": "Email Gate", "kind": "native", "blurb": "Drafts stay gated until a human approves."},
    {"id": "proposals", "name": "Proposal Intelligence", "kind": "native", "blurb": "Scope of work and pricing tiers."},
    {"id": "sandbox", "name": "Blast Radius Sandbox", "kind": "native", "blurb": "Isolated site audits. Host secrets stay out."},
    {"id": "discovery", "name": "Lead Discovery", "kind": "native", "blurb": "Multi-channel prospect search."},
    {"id": "inference", "name": "Vultr Inference", "kind": "model", "blurb": "Serverless model for open questions."},
    {"id": "voice", "name": "Shimmer Voice", "kind": "browser", "blurb": "Microphone and spoken replies in the browser."},
    {"id": "webhook", "name": "Outbound Webhook", "kind": "external", "blurb": "POST a team digest to your own HTTPS endpoint."},
]

CLOSED = "('WON','LOST','OPTED_OUT')"


def ensure():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS team_members(
          id INTEGER PRIMARY KEY,
          name TEXT, role TEXT, skills TEXT, email TEXT,
          capacity INT, status TEXT);
        CREATE TABLE IF NOT EXISTS assignments(
          lead_id INTEGER PRIMARY KEY,
          member_id INT, task TEXT, due TEXT);
        CREATE TABLE IF NOT EXISTS connectors(
          id TEXT PRIMARY KEY, enabled INT DEFAULT 1, config TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS voice_log(
          id INTEGER PRIMARY KEY, at TEXT, agent TEXT, utterance TEXT, reply TEXT);
        """)
        if c.execute("select count(*) from team_members").fetchone()[0] == 0:
            c.executemany(
                "insert into team_members(name,role,skills,email,capacity,status) values(?,?,?,?,?,?)",
                TEAM,
            )
        for spec in CONNECTORS:
            enabled = 0 if spec["id"] == "webhook" else 1
            c.execute(
                "insert or ignore into connectors(id,enabled,config) values(?,?,?)",
                (spec["id"], enabled, ""),
            )
        _sync_assignments(c)


def _task_for(status, score):
    if status in ("NEW", "RESEARCHED"):
        return "Research evidence"
    if status == "QUALIFIED":
        return "Draft outreach"
    if status in ("EMAIL_DRAFTED", "APPROVED"):
        return "Review and send"
    if status in ("CONTACTED", "FOLLOW_UP"):
        return "Follow up"
    if status == "REPLIED":
        return "Book the meeting"
    if status in ("MEETING", "PROPOSAL"):
        return "Advance the proposal"
    return "Review" if (score or 0) >= 60 else "Qualify"


def _due_for(status, score, discovered):
    today = date.today()
    born = today
    if discovered:
        try:
            born = date.fromisoformat(str(discovered)[:10])
        except ValueError:
            born = today
    if status in ("NEW", "RESEARCHED") and (today - born).days >= 3:
        return (born + timedelta(days=3)).isoformat()
    return (today + timedelta(days=2 if (score or 0) >= 75 else 5)).isoformat()


def _sync_assignments(c):
    members = [r[0] for r in c.execute("select id from team_members order by id")]
    if not members:
        return
    rows = c.execute(
        "select id, status, score, discovered_at from leads where id not in (select lead_id from assignments)"
    ).fetchall()
    for i, (lid, status, score, discovered) in enumerate(rows):
        c.execute(
            "insert into assignments(lead_id,member_id,task,due) values(?,?,?,?)",
            (lid, members[i % len(members)], _task_for(status, score), _due_for(status, score, discovered)),
        )
    stale = c.execute(
        "select l.id, l.status, l.score, l.discovered_at from leads l "
        "join assignments a on a.lead_id=l.id where l.status in ('NEW','RESEARCHED')"
    ).fetchall()
    for lid, status, score, discovered in stale:
        c.execute(
            "update assignments set due=?, task=? where lead_id=?",
            (_due_for(status, score, discovered), _task_for(status, score), lid),
        )


def _enabled(c, cid):
    row = c.execute("select enabled, config from connectors where id=?", (cid,)).fetchone()
    if not row:
        return True, ""
    return bool(row[0]), row[1] or ""


def _members(c):
    return [dict(r) for r in c.execute("""
        select m.*,
          (select count(*) from assignments a
             join leads l on l.id=a.lead_id
            where a.member_id=m.id and l.status not in """ + CLOSED + """) as load
        from team_members m order by m.id
    """)]


def _metrics(c):
    total = c.execute("select count(*) from leads").fetchone()[0]
    won = c.execute("select count(*) from leads where status='WON'").fetchone()[0]
    active = c.execute(f"select count(*) from leads where status not in {CLOSED}").fetchone()[0]
    delayed = c.execute(
        f"select count(*) from assignments a join leads l on l.id=a.lead_id "
        f"where a.due < ? and l.status not in {CLOSED}",
        (date.today().isoformat(),),
    ).fetchone()[0]
    progressed = c.execute(
        "select count(*) from leads where status in ('QUALIFIED','EMAIL_DRAFTED','APPROVED','CONTACTED','FOLLOW_UP','REPLIED','MEETING','PROPOSAL','WON')"
    ).fetchone()[0]
    progress = int(round(100 * progressed / total)) if total else 0
    delay_pct = int(round(100 * delayed / active)) if active else 0
    return {
        "progress": progress,
        "delayed": delayed,
        "delay_pct": delay_pct,
        "active": active,
        "won": won,
        "total": total,
    }


def connector_state():
    ensure()
    usage = {}
    try:
        usage = agent.usage()
    except Exception:
        usage = {"configured": False, "provider": "None", "model": ""}
    with conn() as c:
        flags = {r["id"]: dict(r) for r in c.execute("select * from connectors")}
    out = []
    for spec in CONNECTORS:
        row = flags.get(spec["id"], {"enabled": 1, "config": ""})
        live = bool(row["enabled"])
        detail = spec["blurb"]
        if spec["id"] == "inference":
            live = live and bool(usage.get("configured"))
            detail = f"{usage.get('provider', 'None')} · {usage.get('model', 'unset')}"
        elif spec["id"] == "webhook":
            detail = row["config"] or "Add an HTTPS URL to deliver team digests."
        out.append({
            "id": spec["id"],
            "name": spec["name"],
            "kind": spec["kind"],
            "blurb": spec["blurb"],
            "enabled": bool(row["enabled"]),
            "live": live,
            "config": row["config"] or "",
            "detail": detail,
        })
    return out


def _guard(c, cid):
    enabled, config = _enabled(c, cid)
    if not enabled:
        name = next(x["name"] for x in CONNECTORS if x["id"] == cid)
        return {
            "blocked": True,
            "speak": f"{name} is switched off. Turn that connector on and ask again.",
            "reply": f"**{name}** is disconnected. Enable it in the connector dock, then run this agent again.",
            "actions": [],
        }
    return {"blocked": False, "config": config}


def _companies(rows, n=5):
    bits = []
    for r in rows[:n]:
        bits.append(f"{r['company']} ({r['status'].replace('_', ' ')}, score {r['score']})")
    return "; ".join(bits) if bits else "none yet"


def _pipeline_rows(c, extra="", args=(), limit=8):
    sql = f"select company, domain, status, score, service from leads where 1=1 {extra} order by score desc limit ?"
    return [dict(r) for r in c.execute(sql, list(args) + [limit])]


def agent_timeline(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    stages = [dict(r) for r in c.execute(
        "select status, count(*) n from leads group by status order by n desc"
    )]
    lines = [f"• **{s['status'].replace('_', ' ')}** — {s['n']}" for s in stages] or ["• Pipeline is empty."]
    reply = (
        f"**Project timeline** is {m['progress']}% through qualification or later "
        f"({m['won']} won of {m['total']}).\n\n" + "\n".join(lines)
    )
    return {
        "speak": f"The pipeline is {m['progress']} percent of the way through active stages, with {m['won']} deals won.",
        "reply": reply,
        "actions": [{"label": "Open pipeline", "type": "view", "target": "board"}],
    }


def agent_delayed(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        f"""select l.company, l.status, l.score, a.task, a.due, m.name owner
            from assignments a
            join leads l on l.id=a.lead_id
            join team_members m on m.id=a.member_id
            where a.due < ? and l.status not in {CLOSED}
            order by a.due limit 8""",
        (today,),
    )]
    if not rows:
        return {
            "speak": "Nothing is past due. The open work is still inside its window.",
            "reply": "**What's delayed:** no open assignments are past their due date.",
            "actions": [{"label": "Open pipeline", "type": "view", "target": "table"}],
        }
    lines = [f"• **{r['company']}** — {r['task']} · due {r['due']} · {r['owner']}" for r in rows]
    return {
        "speak": f"{len(rows)} items are past due. The first is {rows[0]['company']}, owned by {rows[0]['owner']}.",
        "reply": f"**What's delayed** ({len(rows)} past due):\n\n" + "\n".join(lines),
        "actions": [{"label": "Review pipeline", "type": "view", "target": "table"}],
    }


def agent_ownership(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        f"""select m.name, m.role, count(a.lead_id) n
            from team_members m
            left join assignments a on a.member_id=m.id
            left join leads l on l.id=a.lead_id and l.status not in {CLOSED}
            group by m.id order by n desc"""
    )]
    lines = [f"• **{r['name']}** ({r['role']}) — {r['n']} open" for r in rows]
    top = rows[0]["name"] if rows else "nobody"
    return {
        "speak": f"Ownership is spread across the team. {top} is carrying the most open work.",
        "reply": "**Task ownership**\n\n" + "\n".join(lines),
        "actions": [{"label": "See team", "type": "view", "target": "voice"}],
    }


def agent_pending(c, _q):
    g = _guard(c, "email")
    if g["blocked"]:
        return g
    drafts = c.execute("select count(*) from emails where status='draft'").fetchone()[0]
    fresh = c.execute("select count(*) from leads where status in ('NEW','RESEARCHED')").fetchone()[0]
    quiet = c.execute(
        "select count(*) from leads where score>=60 and id not in (select lead_id from emails)"
    ).fetchone()[0]
    return {
        "speak": f"You have {drafts} drafts waiting, {fresh} leads still in research, and {quiet} qualified leads with no email yet.",
        "reply": (
            "**Pending tasks**\n\n"
            f"• **{drafts}** email drafts waiting for approval\n"
            f"• **{fresh}** leads still in New or Researched\n"
            f"• **{quiet}** scored leads with no draft yet"
        ),
        "actions": [
            {"label": "Email approvals", "type": "view", "target": "approvals"},
            {"label": "Pipeline", "type": "view", "target": "table"},
        ],
    }


def agent_weekly(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    mrr = c.execute("select coalesce(sum(actual_mrr),0) from leads where status='WON'").fetchone()[0]
    pipe = c.execute(
        f"select coalesce(sum(monthly_retainer),0) from leads where status not in {CLOSED}"
    ).fetchone()[0]
    return {
        "speak": f"Weekly picture: {m['active']} live deals, {m['won']} won, {mrr} dollars in closed monthly revenue.",
        "reply": (
            "**Weekly report**\n\n"
            f"• Live deals: **{m['active']}**\n"
            f"• Won: **{m['won']}**\n"
            f"• Closed MRR: **${mrr:,}**\n"
            f"• Open retainer value: **${pipe:,}**\n"
            f"• Stage progress: **{m['progress']}%**"
        ),
        "actions": [{"label": "Pipeline board", "type": "view", "target": "board"}],
    }


def agent_daily(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    today = date.today().isoformat()
    due = c.execute(
        f"""select count(*) from assignments a join leads l on l.id=a.lead_id
            where a.due<=? and l.status not in {CLOSED}""",
        (today,),
    ).fetchone()[0]
    high = c.execute(
        f"select count(*) from leads where score>=75 and status not in {CLOSED}"
    ).fetchone()[0]
    drafts = c.execute("select count(*) from emails where status='draft'").fetchone()[0]
    return {
        "speak": f"Today: {due} items due, {high} high-priority deals, {drafts} drafts to review.",
        "reply": (
            f"**Daily summary** · {today}\n\n"
            f"• Due today or earlier: **{due}**\n"
            f"• High-priority open deals: **{high}**\n"
            f"• Drafts awaiting sign-off: **{drafts}**"
        ),
        "actions": [{"label": "Approvals", "type": "view", "target": "approvals"}],
    }


def agent_planner(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    people = _members(c)
    open_people = [p for p in people if p["status"] == "available" and p["load"] < p["capacity"]]
    focus = [dict(r) for r in c.execute(
        f"""select l.company, l.score, a.task, m.name
            from assignments a join leads l on l.id=a.lead_id
            join team_members m on m.id=a.member_id
            where l.status not in {CLOSED}
            order by l.score desc limit 3"""
    )]
    names = ", ".join(p["name"].split()[0] for p in open_people[:3]) or "no one"
    lines = [f"• **{r['company']}** ({r['score']}) — {r['task']} · {r['name']}" for r in focus]
    return {
        "speak": f"Plan the day around the top deals. {names} can take new work.",
        "reply": "**Team day planner**\n\nFree capacity: " + (names or "none") + "\n\n" + ("\n".join(lines) or "No open work."),
        "actions": [{"label": "High-score pipeline", "type": "view", "target": "table"}],
    }


def agent_activity(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        """select e.kind, e.detail, e.at, l.company
           from events e left join leads l on l.id=e.lead_id
           order by e.id desc limit 6"""
    )]
    if not rows:
        return {
            "speak": "No team activity is logged yet. Research or status changes will show up here.",
            "reply": "**Member activity:** the event log is empty.",
            "actions": [{"label": "Open pipeline", "type": "view", "target": "table"}],
        }
    lines = [f"• {r['at'][:16]} · **{r['company'] or 'system'}** — {r['kind']}: {r['detail'][:80]}" for r in rows]
    return {
        "speak": f"Latest activity is {rows[0]['kind']} on {rows[0]['company'] or 'the system'}.",
        "reply": "**Member activity**\n\n" + "\n".join(lines),
        "actions": [{"label": "Pipeline", "type": "view", "target": "table"}],
    }


def agent_available(c, _q):
    people = [p for p in _members(c) if p["status"] == "available" and p["load"] < p["capacity"]]
    if not people:
        return {
            "speak": "Nobody is under capacity right now.",
            "reply": "**Who is available:** every member is busy or at capacity.",
            "actions": [],
        }
    lines = [f"• **{p['name']}** · {p['role']} · {p['load']}/{p['capacity']}" for p in people]
    return {
        "speak": f"{people[0]['name']} is available, along with {max(0, len(people)-1)} others.",
        "reply": "**Who is available**\n\n" + "\n".join(lines),
        "actions": [],
    }


def agent_busy(c, _q):
    people = [p for p in _members(c) if p["status"] == "busy" or p["load"] >= p["capacity"]]
    if not people:
        return {
            "speak": "No one is marked busy.",
            "reply": "**Who is busy:** the roster is clear.",
            "actions": [],
        }
    lines = [f"• **{p['name']}** · {p['role']} · load {p['load']}/{p['capacity']} · {p['status']}" for p in people]
    return {
        "speak": f"{people[0]['name']} is busy. {len(people)} people are at or over their line.",
        "reply": "**Who is busy**\n\n" + "\n".join(lines),
        "actions": [],
    }


def agent_workload(c, _q):
    people = _members(c)
    lines = []
    for p in people:
        cap = max(1, p["capacity"])
        pct = min(100, int(round(100 * p["load"] / cap)))
        lines.append(f"• **{p['name']}** — {p['load']}/{p['capacity']} ({pct}%)")
    busiest = max(people, key=lambda p: p["load"]) if people else None
    speak = "Workload is balanced." if not busiest else f"{busiest['name']} has the heaviest load at {busiest['load']} open items."
    return {"speak": speak, "reply": "**Current workload**\n\n" + "\n".join(lines), "actions": []}


def agent_progress(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    return {
        "speak": f"Team progress is {m['progress']} percent of deals out of the early stages.",
        "reply": (
            f"**Team progress:** **{m['progress']}%** of leads have reached qualification or beyond.\n\n"
            f"• Active: {m['active']}\n• Won: {m['won']}\n• Past due: {m['delayed']}"
        ),
        "actions": [{"label": "Kanban", "type": "view", "target": "board"}],
    }


def agent_performance(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        """select m.name, m.role,
              sum(case when l.status='WON' then 1 else 0 end) won,
              coalesce(sum(case when l.status='WON' then l.actual_mrr else 0 end),0) mrr
           from team_members m
           left join assignments a on a.member_id=m.id
           left join leads l on l.id=a.lead_id
           group by m.id order by mrr desc, won desc"""
    )]
    lines = [f"• **{r['name']}** — {r['won']} won · ${r['mrr']:,}/mo" for r in rows]
    lead = rows[0]["name"] if rows else "the team"
    return {
        "speak": f"On closed revenue, {lead} is leading the board.",
        "reply": "**Team performance**\n\n" + "\n".join(lines),
        "actions": [{"label": "Won deals", "type": "view", "target": "table"}],
    }


def agent_roster(c, _q):
    lines = [f"• **{p['name']}** — {p['role']} · {p['status']} · {p['skills']}" for p in _members(c)]
    return {
        "speak": f"The team has {len(lines)} specialists covering discovery through close.",
        "reply": "**Team members**\n\n" + "\n".join(lines),
        "actions": [],
    }


def agent_find(c, q):
    people = _members(c)
    words = [w for w in q.lower().split() if len(w) > 2 and w not in {"find", "who", "team", "member", "the", "for"}]
    hits = []
    for p in people:
        blob = f"{p['name']} {p['role']} {p['skills']}".lower()
        if any(w in blob for w in words) or not words:
            hits.append(p)
    if words:
        hits = [p for p in people if any(w in f"{p['name']} {p['role']} {p['skills']}".lower() for w in words)]
    if not hits:
        return {
            "speak": "I could not match that to anyone on the roster.",
            "reply": "**Find team member:** no match. Try a role such as proposals, outreach, or sandbox.",
            "actions": [],
        }
    lines = [f"• **{p['name']}** — {p['role']} · {p['email']}" for p in hits]
    return {
        "speak": f"That matches {hits[0]['name']}, {hits[0]['role']}.",
        "reply": "**Find team member**\n\n" + "\n".join(lines),
        "actions": [],
    }


def agent_suggest(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    lead = c.execute(
        f"select id, company, score, service, status from leads where status not in {CLOSED} order by score desc limit 1"
    ).fetchone()
    if not lead:
        return {
            "speak": "There is no open lead to assign.",
            "reply": "**Suggest member:** add a lead first.",
            "actions": [{"label": "Lead discovery", "type": "view", "target": "lead_finder"}],
        }
    people = [p for p in _members(c) if p["status"] == "available" and p["load"] < p["capacity"]] or _members(c)
    service = (lead["service"] or "").lower()
    def rank(p):
        skills = p["skills"].lower()
        bonus = 2 if service and any(s in service for s in skills.split(",")) else 0
        if "proposal" in service or lead["status"] in ("MEETING", "PROPOSAL"):
            bonus += 3 if "sow" in skills else 0
        return (p["capacity"] - p["load"]) + bonus
    pick = max(people, key=rank)
    return {
        "speak": f"Give {lead['company']} to {pick['name']}. They have room and the closest skill match.",
        "reply": (
            f"**Suggest member**\n\n"
            f"**{lead['company']}** (score {lead['score']}, {lead['status']}) → **{pick['name']}**, {pick['role']}.\n"
            f"Load after this would be about {pick['load'] + 1} of {pick['capacity']}."
        ),
        "actions": [{"label": "Open pipeline", "type": "view", "target": "table"}],
        "assign": {"lead_id": lead["id"], "member_id": pick["id"]},
    }


def agent_email(c, _q):
    g = _guard(c, "email")
    if g["blocked"]:
        return g
    drafts = c.execute("select count(*) from emails where status='draft'").fetchone()[0]
    sent = c.execute("select count(*) from emails where status='sent'").fetchone()[0]
    people = ", ".join(p["name"].split()[0] for p in _members(c))
    digest = (
        f"Team digest for {date.today().isoformat()}: {drafts} drafts waiting, {sent} sent. "
        f"Roster: {people}."
    )
    note = "Digest prepared. It stays inside the app until you approve a draft."
    hook = _guard(c, "webhook")
    if not hook["blocked"] and hook["config"].startswith("http"):
        posted = _post_webhook(hook["config"], {"text": digest, "at": now()})
        note = "Digest posted to the outbound webhook." if posted else "Webhook call failed. Digest kept locally."
    return {
        "speak": f"Team email digest is ready. {drafts} drafts still need a human approval before anything is sent.",
        "reply": f"**Team email**\n\n{digest}\n\n{note}",
        "actions": [{"label": "Review drafts", "type": "view", "target": "approvals"}],
    }


def _post_webhook(url, payload):
    if not url.startswith("https://") and not url.startswith("http://"):
        return False
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Leado-Shimmer/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as res:
            return 200 <= res.status < 300
    except Exception:
        return False


def agent_sandbox(c, _q):
    g = _guard(c, "sandbox")
    if g["blocked"]:
        return g
    import sandbox
    proof = sandbox.sandbox.verify_containment_proof()
    status = proof.get("status", "UNKNOWN")
    return {
        "speak": f"Sandbox containment is {status}. Host secrets stay outside the audit runtime.",
        "reply": (
            f"**Sandbox** status **{status}**.\n\n"
            "Memory 256MB, half a vCPU, 8 second kill, secrets scrubbed."
        ),
        "actions": [{"label": "Open sandbox", "type": "view", "target": "sandbox"}],
    }


def agent_how(c, _q):
    return {
        "speak": "Speak or tap an intent. I hand it to one specialist, that specialist reads a live connector, and I read the answer back. Email stays gated until you approve it.",
        "reply": (
            "**How it works**\n\n"
            "1. You talk, type, or tap an intent on the rail.\n"
            "2. Shimmer routes it to one specialist, or a short chain when you join two asks with “and”.\n"
            "3. The specialist reads a connector: pipeline, email gate, proposals, sandbox, or discovery.\n"
            "4. The spoken answer uses only what that connector returned.\n"
            "5. Drafts are never sent from here. Approve them in Email Approvals."
        ),
        "actions": [{"label": "Email approvals", "type": "view", "target": "approvals"}],
    }


def agent_briefing(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    high = c.execute(f"select count(*) from leads where score>=75 and status not in {CLOSED}").fetchone()[0]
    drafts = c.execute("select count(*) from emails where status='draft'").fetchone()[0]
    meetings = c.execute("select count(*) from leads where status='MEETING'").fetchone()[0]
    return {
        "speak": f"Daily briefing. {m['active']} live deals, {high} high priority, {meetings} meetings, {drafts} drafts waiting, {m['delayed']} past due.",
        "reply": (
            "**Daily briefing**\n\n"
            f"• Live deals: **{m['active']}**\n"
            f"• High priority: **{high}**\n"
            f"• Meetings on the board: **{meetings}**\n"
            f"• Drafts waiting: **{drafts}**\n"
            f"• Past due: **{m['delayed']}**\n"
            f"• Stage progress: **{m['progress']}%**"
        ),
        "actions": [{"label": "Open pipeline", "type": "view", "target": "board"}],
    }


def agent_scrum(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    lines = []
    for p in _members(c):
        top = c.execute(
            f"""select l.company, a.task from assignments a
                join leads l on l.id=a.lead_id
                where a.member_id=? and l.status not in {CLOSED}
                order by l.score desc limit 1""",
            (p["id"],),
        ).fetchone()
        focus = f"{top['company']} — {top['task']}" if top else "no open deal"
        lines.append(f"• **{p['name']}** · {p['role']} · {p['status']} · {focus}")
    return {
        "speak": "Scrum is ready. Each specialist has one focus deal, starting with whoever is first on the roster.",
        "reply": "**Scrum meeting**\n\n" + "\n".join(lines),
        "actions": [],
    }


def agent_leads(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        f"select company, status, score, industry from leads where status not in {CLOSED} order by score desc limit 6"
    )]
    lines = [f"• **{r['company']}** — {r['status'].replace('_', ' ')} · {r['score']} · {r['industry'] or 'industry unknown'}" for r in rows]
    speak = "The lead list is empty." if not rows else f"Top lead is {rows[0]['company']} at score {rows[0]['score']}."
    return {"speak": speak, "reply": "**Leads**\n\n" + ("\n".join(lines) or "• None yet."),
            "actions": [{"label": "Open leads", "type": "view", "target": "table"}]}


def agent_sales_pipeline(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    stages = [dict(r) for r in c.execute("select status, count(*) n from leads group by status order by n desc")]
    pipe = c.execute(f"select coalesce(sum(monthly_retainer),0) from leads where status not in {CLOSED}").fetchone()[0]
    lines = [f"• **{s['status'].replace('_', ' ')}** — {s['n']}" for s in stages] or ["• Pipeline is empty."]
    return {
        "speak": f"Sales pipeline holds {pipe} dollars in open retainer value across {len(stages)} stages.",
        "reply": f"**Sales pipeline** · open retainer **${pipe:,}**\n\n" + "\n".join(lines),
        "actions": [{"label": "Kanban", "type": "view", "target": "board"}],
    }


def agent_priority(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        f"select company, score, status, service from leads where score>=75 and status not in {CLOSED} order by score desc limit 6"
    )]
    lines = [f"• **{r['company']}** — {r['score']} · {r['status'].replace('_', ' ')} · {(r['service'] or 'unset').replace('_', ' ')}" for r in rows]
    speak = "No high-priority leads right now." if not rows else f"{len(rows)} priority leads. First is {rows[0]['company']}."
    return {"speak": speak, "reply": "**Priority leads** (score 75+)\n\n" + ("\n".join(lines) or "• None."),
            "actions": [{"label": "Pipeline", "type": "view", "target": "table"}]}


def agent_proposals(c, _q):
    g = _guard(c, "proposals")
    if g["blocked"]:
        return g
    import proposal_engine
    rows = proposal_engine.list_proposals()
    if not rows:
        top = c.execute(f"select company, score from leads where score>=60 and status not in {CLOSED} order by score desc limit 1").fetchone()
        hint = f" Best next proposal is {top['company']}." if top else ""
        return {
            "speak": "No proposals have been generated yet." + hint,
            "reply": "**Proposals:** none saved yet." + hint,
            "actions": [{"label": "Proposal desk", "type": "view", "target": "proposals"}],
        }
    lines = [f"• **{r.get('company') or 'Lead'}** — {r.get('title') or 'Proposal'} · ${r.get('est_value') or 0:,}/mo" for r in rows[:6]]
    return {
        "speak": f"{len(rows)} proposals on file. The latest is for {rows[0].get('company') or 'a lead'}.",
        "reply": "**Proposals**\n\n" + "\n".join(lines),
        "actions": [{"label": "Open proposals", "type": "view", "target": "proposals"}],
    }


def agent_messages(c, _q):
    g = _guard(c, "email")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        """select e.subject, e.status, l.company from emails e
           join leads l on l.id=e.lead_id order by e.id desc limit 6"""
    )]
    lines = [f"• **{r['company']}** — {r['status']} · {r['subject'] or 'no subject'}" for r in rows]
    speak = "No client messages yet." if not rows else f"Latest message is {rows[0]['status']} for {rows[0]['company']}."
    return {"speak": speak, "reply": "**Client messages**\n\n" + ("\n".join(lines) or "• None yet."),
            "actions": [{"label": "Approvals", "type": "view", "target": "approvals"}]}


def agent_unread(c, _q):
    g = _guard(c, "email")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        """select e.subject, l.company from emails e join leads l on l.id=e.lead_id
           where e.status='draft' order by e.id desc limit 6"""
    )]
    lines = [f"• **{r['company']}** — {r['subject'] or 'untitled draft'}" for r in rows]
    return {
        "speak": f"{len(rows)} unread drafts are waiting for a human." if rows else "The unread queue is clear.",
        "reply": "**Unread emails** (drafts not yet approved)\n\n" + ("\n".join(lines) or "• Inbox is clear."),
        "actions": [{"label": "Review drafts", "type": "view", "target": "approvals"}],
    }


def agent_appointments(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        "select company, score, contact_name, status from leads where status in ('MEETING','FOLLOW_UP','REPLIED') order by score desc limit 6"
    )]
    lines = [f"• **{r['company']}** — {r['status'].replace('_', ' ')} · {r['contact_name'] or 'contact unknown'}" for r in rows]
    speak = "No appointments on the board." if not rows else f"{rows[0]['company']} is the next meeting-stage account."
    return {"speak": speak, "reply": "**Appointments**\n\n" + ("\n".join(lines) or "• None scheduled."),
            "actions": [{"label": "Pipeline", "type": "view", "target": "board"}]}


def agent_clients(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        """select company, status, actual_mrr, client_status from leads
           where status='WON' or client_status in ('active','paused','churned')
           order by actual_mrr desc limit 6"""
    )]
    lines = [f"• **{r['company']}** — {r['status']} · {r['client_status'] or 'prospect'} · ${r['actual_mrr'] or 0:,}/mo" for r in rows]
    speak = "No closed clients yet." if not rows else f"{len(rows)} client records. {rows[0]['company']} leads on recorded revenue."
    return {"speak": speak, "reply": "**Clients**\n\n" + ("\n".join(lines) or "• None yet."),
            "actions": [{"label": "Pipeline", "type": "view", "target": "table"}]}


def agent_wrong(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    lost = [dict(r) for r in c.execute(
        "select company, status, problem from leads where status in ('LOST','OPTED_OUT') limit 4"
    )]
    weak = [dict(r) for r in c.execute(
        f"select company, score, problem from leads where score<50 and status not in {CLOSED} order by score limit 4"
    )]
    today = date.today().isoformat()
    late = [dict(r) for r in c.execute(
        f"""select l.company, a.task, a.due from assignments a join leads l on l.id=a.lead_id
            where a.due < ? and l.status not in {CLOSED} limit 4""",
        (today,),
    )]
    chunks = []
    if lost:
        chunks.append("**Lost or opted out**\n" + "\n".join(f"• **{r['company']}** — {(r['problem'] or 'no note')[:120]}" for r in lost))
    if late:
        chunks.append("**Past due**\n" + "\n".join(f"• **{r['company']}** — {r['task']} · due {r['due']}" for r in late))
    if weak:
        chunks.append("**Thin evidence**\n" + "\n".join(f"• **{r['company']}** — score {r['score']}" for r in weak))
    if not chunks:
        return {"speak": "Nothing is flagged. No lost deals, no past-due work, and no thin scores.",
                "reply": "**What went wrong:** the board is clean.", "actions": []}
    return {
        "speak": "I found issues on the board. The transcript lists lost deals, past-due work, and thin scores.",
        "reply": "**What went wrong**\n\n" + "\n\n".join(chunks),
        "actions": [{"label": "Review pipeline", "type": "view", "target": "table"}],
    }


def agent_fix(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        f"""select company, score, service, problem, status from leads
            where status not in {CLOSED} order by score desc limit 4"""
    )]
    if not rows:
        return {"speak": "There is no open deal to recommend a fix for.", "reply": "**Fix recommendation:** add a lead first.", "actions": []}
    lines = []
    for r in rows:
        problem = (r["problem"] or "No verified problem on file yet").strip()
        service = (r["service"] or "a scoped engagement").replace("_", " ")
        if r["status"] in ("NEW", "RESEARCHED"):
            step = "Run research before promising a fix."
        elif r["status"] in ("EMAIL_DRAFTED", "APPROVED"):
            step = "Get the draft approved before anything is sent."
        elif r["status"] in ("MEETING", "PROPOSAL"):
            step = "Turn the finding into a scoped proposal."
        else:
            step = f"Offer {service}."
        lines.append(f"• **{r['company']}** ({r['score']}) — {problem[:140]}\n  Next: {step}")
    return {
        "speak": f"First fix: {rows[0]['company']}. I kept the recommendation tied to the recorded problem.",
        "reply": "**Fix recommendation**\n\n" + "\n".join(lines),
        "actions": [{"label": "Open top lead", "type": "view", "target": "table"}],
    }


def agent_project_status(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    return {
        "speak": f"Project status is {m['progress']} percent through later stages, with {m['active']} deals still open and {m['won']} won.",
        "reply": (
            "**Project status**\n\n"
            f"• Progress: **{m['progress']}%**\n"
            f"• Open: **{m['active']}**\n"
            f"• Won: **{m['won']}**\n"
            f"• Past due: **{m['delayed']}**\n"
            f"• Records: **{m['total']}**"
        ),
        "actions": [{"label": "Kanban", "type": "view", "target": "board"}],
    }


def agent_my_tasks(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    rows = [dict(r) for r in c.execute(
        f"""select l.company, l.score, a.task, a.due, m.name
            from assignments a join leads l on l.id=a.lead_id
            join team_members m on m.id=a.member_id
            where l.status not in {CLOSED}
            order by a.due limit 8"""
    )]
    lines = [f"• **{r['due']}** · {r['task']} · **{r['company']}** · {r['name']}" for r in rows]
    speak = "No open tasks." if not rows else f"Next task is {rows[0]['task']} for {rows[0]['company']}, due {rows[0]['due']}."
    return {"speak": speak, "reply": "**My tasks**\n\n" + ("\n".join(lines) or "• Queue is clear."),
            "actions": [{"label": "Pipeline", "type": "view", "target": "table"}]}


def agent_sprint(c, _q):
    g = _guard(c, "crm")
    if g["blocked"]:
        return g
    m = _metrics(c)
    drafts = c.execute("select count(*) from emails where status='draft'").fetchone()[0]
    sent = c.execute("select count(*) from emails where status='sent'").fetchone()[0]
    meetings = c.execute("select count(*) from leads where status in ('MEETING','PROPOSAL','WON')").fetchone()[0]
    return {
        "speak": f"Sprint summary. Progress {m['progress']} percent, {meetings} deals in late stages, {sent} emails sent, {drafts} still in draft.",
        "reply": (
            "**Sprint summary**\n\n"
            f"• Stage progress: **{m['progress']}%**\n"
            f"• Late-stage deals: **{meetings}**\n"
            f"• Emails sent: **{sent}**\n"
            f"• Drafts still open: **{drafts}**\n"
            f"• Past due: **{m['delayed']}**\n"
            f"• Won: **{m['won']}**"
        ),
        "actions": [{"label": "Weekly board", "type": "view", "target": "board"}],
    }


def agent_discover(c, _q):
    g = _guard(c, "discovery")
    if g["blocked"]:
        return g
    n = c.execute("select count(*) from leads").fetchone()[0]
    return {
        "speak": f"Discovery is connected. The pipeline currently holds {n} leads.",
        "reply": f"**Lead discovery** is ready. **{n}** companies are already in the pipeline.",
        "actions": [{"label": "Open discovery", "type": "view", "target": "lead_finder"}],
    }


AGENTS = [
    {"id": "timeline", "label": "Project Timeline", "persona": "Chronos", "connector": "crm",
     "keys": ("timeline", "schedule", "roadmap", "stages", "kanban"), "run": agent_timeline},
    {"id": "delayed", "label": "What's Delayed", "persona": "Lag", "connector": "crm",
     "keys": ("delay", "late", "overdue", "stuck", "behind"), "run": agent_delayed},
    {"id": "ownership", "label": "Task Ownership", "persona": "Steward", "connector": "crm",
     "keys": ("ownership", "owner", "assigned", "who owns"), "run": agent_ownership},
    {"id": "pending", "label": "Pending Tasks", "persona": "Queue", "connector": "email",
     "keys": ("pending", "todo", "to-do", "waiting", "queue"), "run": agent_pending},
    {"id": "weekly", "label": "Weekly Report", "persona": "Herald", "connector": "crm",
     "keys": ("weekly", "week", "this week"), "run": agent_weekly},
    {"id": "daily", "label": "Daily Summary", "persona": "Dawn", "connector": "crm",
     "keys": ("daily summary", "today", "end of day"), "run": agent_daily},
    {"id": "planner", "label": "Team Day Planner", "persona": "Planner", "connector": "crm",
     "keys": ("plan", "planner", "day plan", "agenda"), "run": agent_planner},
    {"id": "activity", "label": "Member Activity", "persona": "Pulse", "connector": "crm",
     "keys": ("activity", "recent", "happened", "log"), "run": agent_activity},
    {"id": "available", "label": "Who Is Available", "persona": "Open", "connector": "crm",
     "keys": ("available", "free", "who can", "capacity"), "run": agent_available},
    {"id": "busy", "label": "Who Is Busy", "persona": "Load", "connector": "crm",
     "keys": ("busy", "slammed", "overloaded"), "run": agent_busy},
    {"id": "workload", "label": "Current Workload", "persona": "Gauge", "connector": "crm",
     "keys": ("workload", "load", "capacity"), "run": agent_workload},
    {"id": "progress", "label": "Team Progress", "persona": "Momentum", "connector": "crm",
     "keys": ("progress", "momentum", "velocity"), "run": agent_progress},
    {"id": "performance", "label": "Team Performance", "persona": "Scoreboard", "connector": "crm",
     "keys": ("performance", "leaderboard", "quota", "mrr"), "run": agent_performance},
    {"id": "roster", "label": "Team Members", "persona": "Roster", "connector": "crm",
     "keys": ("members", "roster", "team list", "who is on"), "run": agent_roster},
    {"id": "find", "label": "Find Team Member", "persona": "Finder", "connector": "crm",
     "keys": ("find", "search member", "who handles", "who does"), "run": agent_find},
    {"id": "suggest", "label": "Suggest Member", "persona": "Match", "connector": "crm",
     "keys": ("suggest member", "suggest someone", "best person", "who should own"), "run": agent_suggest},
    {"id": "email", "label": "Team Email", "persona": "Dispatch", "connector": "email",
     "keys": ("email", "digest", "notify the team", "inbox"), "run": agent_email},
    {"id": "sandbox", "label": "Sandbox Check", "persona": "Contain", "connector": "sandbox",
     "keys": ("sandbox", "contain", "blast", "security"), "run": agent_sandbox},
    {"id": "discover", "label": "Lead Discovery", "persona": "Scout", "connector": "discovery",
     "keys": ("discover", "find leads", "hunt", "scrape"), "run": agent_discover},
    {"id": "how", "label": "How It Works", "persona": "Vio", "connector": "voice", "lane": "vio",
     "keys": ("how it works", "how do you work", "what can you do"), "run": agent_how},
    {"id": "briefing", "label": "Daily Briefing", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("daily briefing", "briefing", "brief me"), "run": agent_briefing},
    {"id": "scrum", "label": "Scrum Meeting", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("scrum meeting", "scrum", "standup", "stand-up"), "run": agent_scrum},
    {"id": "leads", "label": "Leads", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("show leads", "lead list", "our leads"), "run": agent_leads},
    {"id": "sales_pipeline", "label": "Sales Pipeline", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("sales pipeline", "pipeline"), "run": agent_sales_pipeline},
    {"id": "priority", "label": "Priority Leads", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("priority leads", "priority", "high priority"), "run": agent_priority},
    {"id": "proposals_intent", "label": "Proposals", "persona": "Vio", "connector": "proposals", "lane": "vio",
     "keys": ("proposals", "proposal", "sow"), "run": agent_proposals},
    {"id": "messages", "label": "Client Messages", "persona": "Vio", "connector": "email", "lane": "vio",
     "keys": ("client messages", "messages"), "run": agent_messages},
    {"id": "unread", "label": "Unread Emails", "persona": "Vio", "connector": "email", "lane": "vio",
     "keys": ("unread", "unread emails"), "run": agent_unread},
    {"id": "appointments", "label": "Appointments", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("appointment", "appointments", "meeting"), "run": agent_appointments},
    {"id": "clients", "label": "Clients", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("clients", "customers", "accounts"), "run": agent_clients},
    {"id": "wrong", "label": "What Went Wrong", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("what went wrong", "went wrong", "what broke"), "run": agent_wrong},
    {"id": "fix", "label": "Fix Recommendation", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("fix recommendation", "recommend a fix", "what should we fix"), "run": agent_fix},
    {"id": "project_status", "label": "Project Status", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("project status", "status update"), "run": agent_project_status},
    {"id": "my_tasks", "label": "My Tasks", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("my tasks", "my task"), "run": agent_my_tasks},
    {"id": "sprint", "label": "Sprint Summary", "persona": "Vio", "connector": "crm", "lane": "vio",
     "keys": ("sprint", "sprint summary"), "run": agent_sprint},
]

BY_ID = {a["id"]: a for a in AGENTS}


def _match(utterance):
    q = (utterance or "").lower()
    scored = []
    for spec in AGENTS:
        matched = [k for k in spec["keys"] if k in q]
        if matched:
            scored.append((max(len(k) for k in matched), spec))
    scored.sort(key=lambda item: -item[0])
    if not scored:
        return []
    if " and " in q:
        picked, seen = [], set()
        for _n, spec in scored:
            if spec["id"] in seen:
                continue
            seen.add(spec["id"])
            picked.append(spec)
            if len(picked) == 3:
                break
        return picked
    return [scored[0][1]]


def _remember(agent_id, utterance, reply):
    with conn() as c:
        c.execute(
            "insert into voice_log(at,agent,utterance,reply) values(?,?,?,?)",
            (now(), agent_id, (utterance or "")[:300], reply[:800]),
        )
        c.execute(
            "delete from voice_log where id not in (select id from voice_log order by id desc limit 40)"
        )


def talk(utterance="", agent_id=None):
    ensure()
    utterance = (utterance or "").strip()
    chosen = []
    if agent_id and agent_id in BY_ID:
        chosen = [BY_ID[agent_id]]
    else:
        chosen = _match(utterance)
    if not chosen:
        if utterance and _inference_on():
            return _open_question(utterance)
        return _menu(utterance)

    parts, speaks, actions, workflow = [], [], [], []
    with conn() as c:
        for spec in chosen:
            result = spec["run"](c, utterance)
            if result.get("assign"):
                a = result["assign"]
                c.execute(
                    "insert into assignments(lead_id,member_id,task,due) values(?,?,?,?) "
                    "on conflict(lead_id) do update set member_id=excluded.member_id, task=excluded.task",
                    (a["lead_id"], a["member_id"], "Suggested owner", (date.today() + timedelta(days=2)).isoformat()),
                )
            parts.append(result["reply"])
            speaks.append(result["speak"])
            actions.extend(result.get("actions") or [])
            workflow.append({
                "agent": spec["id"],
                "label": spec["label"],
                "persona": spec["persona"],
                "connector": spec["connector"],
                "ok": not result.get("blocked"),
            })
        metrics = _metrics(c)
    reply = "\n\n".join(parts)
    speak = " ".join(speaks)
    primary = chosen[0]
    _remember(primary["id"], utterance or primary["label"], reply)
    return {
        "agent": primary["id"],
        "label": primary["label"],
        "persona": primary["persona"],
        "speak": speak,
        "reply": reply,
        "actions": _dedupe_actions(actions),
        "workflow": workflow,
        "metrics": metrics,
    }


def _dedupe_actions(actions):
    seen, out = set(), []
    for a in actions:
        key = (a.get("type"), a.get("target"), a.get("label"))
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out[:4]


def _menu(utterance):
    reply = (
        "**Shimmer** is listening. Pick an agent on the rail, or say what you need — "
        "timeline, what's delayed, who is available, a daily summary, or a team email digest."
    )
    if utterance:
        reply = f"I heard “{utterance}”, and it did not match a specialist.\n\n" + reply
    return {
        "agent": "shimmer",
        "label": "Shimmer",
        "persona": "Shimmer",
        "speak": "I am Shimmer. Ask for the timeline, delays, who is free, or a daily summary.",
        "reply": reply,
        "actions": [],
        "workflow": [],
        "metrics": _metrics_safe(),
    }


def _metrics_safe():
    with conn() as c:
        return _metrics(c)


def _inference_on():
    try:
        info = agent.usage()
    except Exception:
        return False
    with conn() as c:
        enabled, _ = _enabled(c, "inference")
    return enabled and info.get("configured")


def _open_question(utterance):
    system = (
        "You are Shimmer, the voice of a sales-operations agent team inside Leado. "
        "Answer in under 90 words. Be concrete. Do not invent pipeline numbers. "
        "If you need data you do not have, say which agent to ask: timeline, delayed, workload, or daily summary."
    )
    try:
        raw = agent.llm(system, utterance, max_tokens=220, temperature=0.3)
        text = raw.get("raw_response") if isinstance(raw, dict) else str(raw)
        text = (text or "").strip() or "I could not form an answer."
    except Exception as ex:
        text = f"Inference is connected but did not answer ({ex}). Use the agent rail for live pipeline data."
    _remember("shimmer", utterance, text)
    return {
        "agent": "shimmer",
        "label": "Shimmer",
        "persona": "Shimmer",
        "speak": text[:280],
        "reply": text,
        "actions": [],
        "workflow": [{"agent": "shimmer", "label": "Shimmer", "persona": "Shimmer", "connector": "inference", "ok": True}],
        "metrics": _metrics_safe(),
    }


def state():
    ensure()
    with conn() as c:
        metrics = _metrics(c)
        team = _members(c)
        recent = [dict(r) for r in c.execute(
            "select at, agent, utterance, reply from voice_log order by id desc limit 8"
        )]
    agents = [{
        "id": a["id"], "label": a["label"], "persona": a["persona"],
        "connector": a["connector"], "lane": a.get("lane", "team"),
    } for a in AGENTS]
    return {"agents": agents, "connectors": connector_state(), "team": team, "metrics": metrics, "recent": recent}


def set_connector(cid, enabled=None, config=None):
    ensure()
    if cid not in {s["id"] for s in CONNECTORS}:
        raise ValueError("unknown connector")
    if config is not None:
        config = str(config).strip()
        if config and cid == "webhook" and not (config.startswith("https://") or config.startswith("http://")):
            raise ValueError("webhook must start with http:// or https://")
        if len(config) > 300:
            raise ValueError("config too long")
    with conn() as c:
        if enabled is not None:
            c.execute("update connectors set enabled=? where id=?", (1 if enabled else 0, cid))
        if config is not None:
            c.execute("update connectors set config=? where id=?", (config, cid))
    return {"ok": True, "connectors": connector_state()}
