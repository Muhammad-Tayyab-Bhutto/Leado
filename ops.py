"""Delivery operations: people, AI agents and the project queue.

The admin (the bearer-token holder) manages one roster that mixes human
employees and AI agents, and a queue of delivery projects. Availability is
derived from real assignments, never typed by hand:

    free         no active projects
    busy         active projects below capacity
    at_capacity  active projects >= capacity
    away         manually marked unavailable (leave, maintenance)

A PayPal payment that settles creates a queued delivery project, so a won deal
flows straight into delivery.
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional

from app import conn, now

KINDS = {"human", "ai_agent"}
PROJECT_STATUSES = ["queued", "in_progress", "review", "done", "on_hold"]
ACTIVE = ("in_progress", "review")
PRIORITIES = {"low": 1, "normal": 2, "high": 3, "urgent": 4}
DEFAULT_CAPACITY = {"human": 2, "ai_agent": 5}

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    lead_id INT,
    payment_id INT UNIQUE,
    skills TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'queued',
    assignee_id INT,
    value REAL,
    due TEXT,
    created_at TEXT, started_at TEXT, completed_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS project_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INT NOT NULL, kind TEXT, detail TEXT, at TEXT
);
"""


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def ensure():
    with conn() as c:
        c.executescript(SCHEMA)
        have = {r[1] for r in c.execute("pragma table_info(team_members)")}
        if not have:  # workforce.ensure() normally creates it; keep ops usable on its own
            c.execute("CREATE TABLE IF NOT EXISTS team_members(id INTEGER PRIMARY KEY, name TEXT, role TEXT, skills TEXT, "
                      "email TEXT, capacity INT, status TEXT)")
            have = {r[1] for r in c.execute("pragma table_info(team_members)")}
        for col, typ in (("kind", "TEXT DEFAULT 'human'"), ("active", "INT DEFAULT 1"), ("project_capacity", "INT"),
                         ("created_at", "TEXT")):
            if col not in have:
                c.execute(f"alter table team_members add column {col} {typ}")


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _text(v, name, max_len=200, required=False):
    v = ("" if v is None else str(v)).strip()
    if required and not v: raise ValueError(f"{name} is required")
    if len(v) > max_len: raise ValueError(f"{name} is too long (max {max_len})")
    return v

def _skills(v) -> str:
    items = v if isinstance(v, list) else str(v or "").split(",")
    clean = sorted({s.strip().lower() for s in items if s and s.strip()})
    if len(clean) > 30: raise ValueError("too many skills (max 30)")
    return ",".join(s[:40] for s in clean)

def _int(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number between {lo} and {hi}")
    return int(v)

def _date(v, name):
    if v in (None, ""): return None
    try: return date.fromisoformat(str(v)).isoformat()
    except ValueError: raise ValueError(f"{name} must be YYYY-MM-DD")

def _log(c, pid, kind, detail):
    c.execute("insert into project_events(project_id,kind,detail,at) values(?,?,?,?)", (pid, kind, str(detail)[:500], now()))


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------

def _capacity(m) -> int:
    return int(m.get("project_capacity") or DEFAULT_CAPACITY.get(m.get("kind") or "human", 2))

def availability(m: Dict[str, Any], active_count: int) -> str:
    if (m.get("status") or "") == "away": return "away"
    if active_count == 0: return "free"
    return "at_capacity" if active_count >= _capacity(m) else "busy"

def list_members(include_inactive=False) -> List[Dict[str, Any]]:
    ensure()
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "select * from team_members" + ("" if include_inactive else " where coalesce(active,1)=1") + " order by coalesce(kind,'human'), name")]
        work = {}
        for p in c.execute(f"select p.id, p.name, p.status, p.assignee_id, p.due, l.company from projects p "
                           f"left join leads l on l.id=p.lead_id where p.status in {ACTIVE} order by p.id"):
            work.setdefault(p["assignee_id"], []).append({"id": p["id"], "name": p["name"], "status": p["status"],
                                                         "due": p["due"], "client": p["company"]})
        queued = {r[0]: r[1] for r in c.execute("select assignee_id, count(*) from projects where status='queued' "
                                                 "and assignee_id is not null group by assignee_id")}
    for m in rows:
        m["kind"] = m.get("kind") or "human"
        m["working_on"] = work.get(m["id"], [])
        m["active_projects"] = len(m["working_on"])
        m["queued_projects"] = queued.get(m["id"], 0)
        m["capacity_projects"] = _capacity(m)
        m["availability"] = availability(m, m["active_projects"]) if m.get("active", 1) else "inactive"
        m["skills_list"] = [s for s in (m.get("skills") or "").split(",") if s]
    return rows

def add_member(b: Dict[str, Any]) -> Dict[str, Any]:
    ensure()
    kind = b.get("kind") or "human"
    if kind not in KINDS: raise ValueError("kind must be 'human' or 'ai_agent'")
    name = _text(b.get("name"), "name", 120, required=True)
    role = _text(b.get("role"), "role", 120, required=True)
    email = _text(b.get("email"), "email", 254)
    if email and not EMAIL_RE.match(email): raise ValueError("invalid email")
    cap = _int(b.get("capacity", DEFAULT_CAPACITY[kind]), "capacity", 1, 50)
    with conn() as c:
        if c.execute("select 1 from team_members where lower(name)=lower(?) and coalesce(active,1)=1", (name,)).fetchone():
            raise ValueError(f"an active member named '{name}' already exists")
        c.execute("insert into team_members(name,role,skills,email,capacity,status,kind,active,project_capacity,created_at) "
                  "values(?,?,?,?,?,?,?,?,?,?)", (name, role, _skills(b.get("skills")), email.lower() or None, cap, "available",
                                                  kind, 1, cap, now()))
        mid = c.execute("select last_insert_rowid()").fetchone()[0]
    return get_member(mid)

def get_member(mid: int) -> Dict[str, Any]:
    m = next((x for x in list_members(include_inactive=True) if x["id"] == mid), None)
    if not m: raise LookupError("member not found")
    return m

def update_member(mid: int, b: Dict[str, Any]) -> Dict[str, Any]:
    ensure()
    sets, args = [], []
    if "name" in b: sets.append("name=?"); args.append(_text(b["name"], "name", 120, required=True))
    if "role" in b: sets.append("role=?"); args.append(_text(b["role"], "role", 120, required=True))
    if "skills" in b: sets.append("skills=?"); args.append(_skills(b["skills"]))
    if "email" in b:
        e = _text(b["email"], "email", 254)
        if e and not EMAIL_RE.match(e): raise ValueError("invalid email")
        sets.append("email=?"); args.append(e.lower() or None)
    if "capacity" in b:
        cap = _int(b["capacity"], "capacity", 1, 50); sets += ["project_capacity=?", "capacity=?"]; args += [cap, cap]
    if "status" in b:
        if b["status"] not in ("available", "away"): raise ValueError("status must be 'available' or 'away'")
        sets.append("status=?"); args.append(b["status"])
    if "active" in b:
        if not isinstance(b["active"], bool): raise ValueError("active must be true or false")
        sets.append("active=?"); args.append(int(b["active"]))
    if not sets: raise ValueError("nothing to update")
    with conn() as c:
        if not c.execute("select 1 from team_members where id=?", (mid,)).fetchone(): raise LookupError("member not found")
        if b.get("active") is False:  # deactivating: hand active/queued work back to the queue
            for p in c.execute("select id, name from projects where assignee_id=? and status in ('queued','in_progress','review')", (mid,)).fetchall():
                c.execute("update projects set assignee_id=null, status='queued', updated_at=? where id=?", (now(), p["id"]))
                _log(c, p["id"], "unassigned", "Owner deactivated; project returned to the queue")
        c.execute(f"update team_members set {', '.join(sets)} where id=?", (*args, mid))
    return get_member(mid)


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def _project_rows(c, where="1=1", args=()):
    rows = c.execute(f"""select p.*, l.company, m.name as assignee_name, m.kind as assignee_kind
                         from projects p left join leads l on l.id=p.lead_id left join team_members m on m.id=p.assignee_id
                         where {where} order by case p.status when 'in_progress' then 0 when 'review' then 1 when 'queued' then 2
                         when 'on_hold' then 3 else 4 end, case p.priority when 'urgent' then 0 when 'high' then 1 when 'normal' then 2 else 3 end,
                         coalesce(p.due,'9999'), p.id""", args).fetchall()
    today = date.today().isoformat()
    out = []
    for r in rows:
        d = dict(r)
        d["overdue"] = bool(d["due"] and d["due"] < today and d["status"] not in ("done",))
        d["skills_list"] = [s for s in (d.get("skills") or "").split(",") if s]
        out.append(d)
    return out

def list_projects(status: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure()
    if status and status not in PROJECT_STATUSES: raise ValueError("unknown status")
    with conn() as c:
        return _project_rows(c, "p.status=?" if status else "1=1", (status,) if status else ())

def get_project(pid: int) -> Dict[str, Any]:
    with conn() as c:
        rows = _project_rows(c, "p.id=?", (pid,))
        if not rows: raise LookupError("project not found")
        rows[0]["events"] = [dict(e) for e in c.execute("select kind, detail, at from project_events where project_id=? order by id desc limit 20", (pid,))]
        return rows[0]

def _check_assignee(c, aid):
    if aid is None: return None
    if isinstance(aid, bool) or not isinstance(aid, int): raise ValueError("assignee_id must be a member id")
    m = c.execute("select id, coalesce(active,1) as active from team_members where id=?", (aid,)).fetchone()
    if not m: raise ValueError("assignee not found")
    if not m["active"]: raise ValueError("assignee is deactivated")
    return aid

def create_project(b: Dict[str, Any]) -> Dict[str, Any]:
    ensure()
    name = _text(b.get("name"), "name", 160, required=True)
    pr = b.get("priority") or "normal"
    if pr not in PRIORITIES: raise ValueError("priority must be low, normal, high or urgent")
    with conn() as c:
        lid = b.get("lead_id")
        if lid is not None and not c.execute("select 1 from leads where id=?", (lid,)).fetchone(): raise ValueError("lead not found")
        aid = _check_assignee(c, b.get("assignee_id"))
        c.execute("insert into projects(name,description,lead_id,skills,priority,status,assignee_id,value,due,created_at,updated_at) "
                  "values(?,?,?,?,?,'queued',?,?,?,?,?)",
                  (name, _text(b.get("description"), "description", 4000), lid, _skills(b.get("skills")), pr, aid,
                   b.get("value"), _date(b.get("due"), "due"), now(), now()))
        pid = c.execute("select last_insert_rowid()").fetchone()[0]
        _log(c, pid, "created", "Project created by admin")
    return get_project(pid)

def update_project(pid: int, b: Dict[str, Any]) -> Dict[str, Any]:
    ensure()
    with conn() as c:
        p = c.execute("select * from projects where id=?", (pid,)).fetchone()
        if not p: raise LookupError("project not found")
        sets, args, notes = ["updated_at=?"], [now()], []
        if "assignee_id" in b:
            aid = _check_assignee(c, b["assignee_id"])
            sets.append("assignee_id=?"); args.append(aid)
            who = c.execute("select name from team_members where id=?", (aid,)).fetchone() if aid else None
            notes.append(("assigned", f"Assigned to {who[0]}" if who else "Unassigned"))
        if "status" in b:
            st = b["status"]
            if st not in PROJECT_STATUSES: raise ValueError("status must be one of " + ", ".join(PROJECT_STATUSES))
            owner = b.get("assignee_id", p["assignee_id"])
            if st in ACTIVE and not owner: raise ValueError("assign someone before starting the project")
            sets.append("status=?"); args.append(st)
            if st == "in_progress" and not p["started_at"]: sets.append("started_at=?"); args.append(now())
            if st == "done": sets.append("completed_at=?"); args.append(now())
            notes.append(("status", f"{p['status']} → {st}"))
        if "priority" in b:
            if b["priority"] not in PRIORITIES: raise ValueError("priority must be low, normal, high or urgent")
            sets.append("priority=?"); args.append(b["priority"])
        if "due" in b: sets.append("due=?"); args.append(_date(b["due"], "due"))
        if "name" in b: sets.append("name=?"); args.append(_text(b["name"], "name", 160, required=True))
        if "skills" in b: sets.append("skills=?"); args.append(_skills(b["skills"]))
        if len(sets) == 1: raise ValueError("nothing to update")
        c.execute(f"update projects set {', '.join(sets)} where id=?", (*args, pid))
        for k, d in notes: _log(c, pid, k, d)
    return get_project(pid)


# ---------------------------------------------------------------------------
# auto-assignment (deterministic, explainable)
# ---------------------------------------------------------------------------

def _score(member, project_skills):
    have = set(member["skills_list"]) | {member.get("role", "").lower()}
    match = len(project_skills & have)
    headroom = member["capacity_projects"] - member["active_projects"] - member["queued_projects"]
    return match, headroom

def auto_assign() -> Dict[str, Any]:
    """Give every unassigned queued project to the best available member: most matching skills, then most
    headroom; humans and AI agents compete on equal terms. Returns each decision with its reason."""
    decisions, skipped = [], []
    queue = [p for p in list_projects("queued") if not p["assignee_id"]]
    queue.sort(key=lambda p: (-PRIORITIES.get(p["priority"], 2), p["due"] or "9999", p["id"]))
    for p in queue:
        members = [m for m in list_members() if m["availability"] in ("free", "busy")]
        want = set(p["skills_list"])
        ranked = sorted(((_score(m, want), m) for m in members), key=lambda x: (-x[0][0], -x[0][1], x[1]["id"]))
        ranked = [(s, m) for s, m in ranked if s[1] > 0 and (s[0] > 0 or not want)]
        if not ranked:
            skipped.append({"project_id": p["id"], "project": p["name"], "reason": "no available member with matching skills and free capacity"})
            continue
        (match, headroom), m = ranked[0]
        update_project(p["id"], {"assignee_id": m["id"]})
        why = (f"{match} matching skill{'s' if match != 1 else ''}" if want else "no specific skills required") + \
              f", {headroom} free slot{'s' if headroom != 1 else ''}"
        with conn() as c: _log(c, p["id"], "auto_assigned", f"Auto-assigned to {m['name']} ({why})")
        decisions.append({"project_id": p["id"], "project": p["name"], "member_id": m["id"], "member": m["name"],
                          "kind": m["kind"], "reason": why})
    return {"assigned": decisions, "skipped": skipped}


# ---------------------------------------------------------------------------
# PayPal hook + summary
# ---------------------------------------------------------------------------

SERVICE_SKILLS = {"web_app": "frontend,backend", "saas": "backend,saas", "mobile": "mobile", "ai_agents": "ai,python",
                  "automation": "automation,python", "erp_crm": "erp,integration", "cms": "cms,frontend",
                  "technical_seo": "seo", "aeo": "seo,content", "geo": "seo,content", "seo_content": "content,seo",
                  "cro": "frontend,analytics", "devops": "devops,security"}

def project_from_payment(c, pay: Dict[str, Any]):
    """Called inside paypal.settle(): queue a delivery project for a newly paid deal (idempotent)."""
    c.executescript(SCHEMA)
    if c.execute("select 1 from projects where payment_id=?", (pay["id"],)).fetchone(): return
    lead = c.execute("select company, service from leads where id=?", (pay["lead_id"],)).fetchone()
    company = lead["company"] if lead else "Client"
    skills = SERVICE_SKILLS.get((lead["service"] if lead else "") or "", "")
    tier = re.sub(r"\s*\(recommended\)", "", pay.get("tier_name") or "Engagement", flags=re.I)
    c.execute("insert into projects(name,description,lead_id,payment_id,skills,priority,status,value,created_at,updated_at) "
              "values(?,?,?,?,?,?, 'queued', ?, ?, ?)",
              (f"{company}: {tier}", f"Created automatically when PayPal {pay['kind']} {pay['paypal_id']} settled.",
               pay["lead_id"], pay["id"], skills, "high", pay["amount"], now(), now()))
    pid = c.execute("select last_insert_rowid()").fetchone()[0]
    _log(c, pid, "created", f"Queued from PayPal payment {pay['paypal_id']}")

def summary() -> Dict[str, Any]:
    members, projects = list_members(), list_projects()
    by = lambda k: sum(1 for m in members if m["availability"] == k)
    ps = lambda s: sum(1 for p in projects if p["status"] == s)
    return {"members": len(members), "humans": sum(1 for m in members if m["kind"] == "human"),
            "ai_agents": sum(1 for m in members if m["kind"] == "ai_agent"),
            "free": by("free"), "busy": by("busy"), "at_capacity": by("at_capacity"), "away": by("away"),
            "queued": ps("queued"), "unassigned_queued": sum(1 for p in projects if p["status"] == "queued" and not p["assignee_id"]),
            "in_progress": ps("in_progress"), "review": ps("review"), "done": ps("done"), "on_hold": ps("on_hold"),
            "overdue": sum(1 for p in projects if p["overdue"])}
