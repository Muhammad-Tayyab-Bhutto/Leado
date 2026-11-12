"""The company model: departments, the client-to-delivery lifecycle, and
department AI agents that produce real work products.

When a project is created (for example by a PayPal payment), the Project
Manager agent plans one task per lifecycle stage and routes each to the
department that owns it. Tasks owned by AI agents can be run: the agent writes
its deliverable (estimate, test plan, runbook, ...) with the LLM, and the task
waits in review until a human approves it. Agents write documents; they do not
ship code or move money.
"""
import json
import os
import re
import threading
from typing import Any, Dict, List

from app import conn, now
import ops

# ---------------------------------------------------------------------------
# Company structure: 20 functional areas in four pillars
# ---------------------------------------------------------------------------

DEPARTMENTS = [
    # id, name, pillar, roles, focus
    ("executive", "Executive Management", "leadership", "CEO, COO, CTO, CFO, CMO", "Strategy, leadership and partnerships"),
    ("bizdev", "Business Development", "revenue", "BDM, BDRs, Partnership Managers", "New markets, partners and referral networks"),
    ("sales", "Sales", "revenue", "Head of Sales, SDRs, Account Executives, Proposal & Bid Specialists, Account Managers",
     "Find, qualify and close clients; collect first payments"),
    ("marketing", "Marketing & Brand", "revenue", "CMO, SEO/AEO/GEO, Content, Social, PPC, Email", "Visibility, content and qualified demand"),
    ("presales", "Business Analysis & Pre-Sales", "revenue", "Solutions Architects, Business Analysts, Estimation Specialists",
     "Requirements, feasibility, estimates and technical proposals"),
    ("engineering", "Software Engineering", "delivery", "CTO, Tech Leads, Frontend, Backend, Mobile, AI/ML, Blockchain",
     "Architecture, APIs, integrations and code"),
    ("design", "UI/UX & Product Design", "delivery", "Head of Design, Product/UI/UX Designers, UX Researchers",
     "Research, user journeys, wireframes and design systems"),
    ("project_management", "Project & Program Management", "delivery", "Project Managers, Scrum Masters, Delivery Managers",
     "Scope, sprints, risks and on-time delivery"),
    ("qa", "Quality Assurance", "delivery", "QA Lead, Manual, Automation, API, Performance & Security Testers",
     "Test plans, regression, UAT and release validation"),
    ("devops", "DevOps & Cloud", "delivery", "DevOps, Cloud (AWS/Azure/GCP), SRE, DBAs", "CI/CD, deployment, monitoring and backups"),
    ("security", "Cybersecurity", "delivery", "CISO, AppSec Engineers, Pentesters, Compliance", "Audits, hardening and incident response"),
    ("product", "Product Management", "product", "Head of Product, Product Managers, Product Owners", "Own products, roadmaps and monetization"),
    ("rnd", "Research & Development", "product", "R&D Director, AI Researchers, Prototype Developers", "Emerging tech, POCs and internal tools"),
    ("data", "Data & Business Intelligence", "operations", "Head of Data, Data Analysts, BI Developers", "Dashboards, forecasting and profitability"),
    ("customer_success", "Customer Support & Success", "operations", "CSMs, Support Engineers, Helpdesk",
     "Support, renewals, satisfaction and upsell"),
    ("finance", "Finance & Accounting", "operations", "CFO, Accountants, Billing Specialists, Payroll", "Invoicing, collections, payroll and P&L"),
    ("hr", "Human Resources & Recruitment", "operations", "HR Manager, Technical Recruiters, L&D", "Hiring, onboarding, reviews and retention"),
    ("legal", "Legal & Compliance", "operations", "Legal Advisor, Contract Manager, Compliance Officer", "Contracts, NDAs, IP and data protection"),
    ("admin", "Administration & Procurement", "operations", "Admin Manager, Procurement & Vendor Managers", "Licences, vendors and purchasing"),
    ("it", "Internal IT & Learning", "operations", "IT Manager, Sysadmins, Technical Trainers", "Devices, access, internal systems and training"),
]
AGENT_NAME = {"executive": "Executive", "bizdev": "BizDev", "sales": "Sales", "marketing": "Marketing", "presales": "Pre-Sales",
              "engineering": "Engineering", "design": "Design", "project_management": "PM", "qa": "QA", "devops": "DevOps",
              "security": "Security", "product": "Product", "rnd": "R&D", "data": "Data", "customer_success": "Customer Success",
              "finance": "Finance", "hr": "HR", "legal": "Legal", "admin": "Procurement", "it": "IT"}
DEPT = {d[0]: {"id": d[0], "name": d[1], "pillar": d[2], "roles": d[3], "focus": d[4]} for d in DEPARTMENTS}
PILLARS = {"leadership": "Leadership", "revenue": "Revenue", "delivery": "Delivery", "product": "Product & R&D", "operations": "Operations"}

# The client-to-delivery lifecycle after a deal is paid. Each stage is owned by one department.
LIFECYCLE = [
    ("requirements", "presales", "Requirement analysis & estimate",
     "Write a requirements summary, key assumptions, and an effort estimate in hours broken down by frontend, backend, QA and DevOps."),
    ("plan", "project_management", "Sprint plan & milestones",
     "Write a sprint plan: 2-week sprints with goals, milestones with dates relative to kickoff, and the top 3 delivery risks with mitigations."),
    ("design", "design", "UX flows & wireframe spec",
     "Write the main user journeys and a screen-by-screen wireframe specification (screens, key components, states)."),
    ("architecture", "engineering", "Architecture & API plan",
     "Write a technical architecture: components, data model outline, main API endpoints, third-party integrations and chosen stack."),
    ("qa", "qa", "Test plan & acceptance criteria",
     "Write a test plan: scope, functional and regression test cases, API tests, and acceptance criteria per milestone."),
    ("security", "security", "Security review checklist",
     "Write a security review: threat model highlights, authentication and authorization checks, data protection, and hardening tasks."),
    ("deploy", "devops", "Deployment & monitoring runbook",
     "Write a deployment runbook: environments, CI/CD steps, monitoring and alerting, backups and rollback procedure."),
    ("handover", "customer_success", "Client handover & success plan",
     "Write a handover plan: demo agenda, documentation to deliver, support SLA, and renewal/upsell opportunities."),
    ("billing", "finance", "Milestone billing plan",
     "Write a milestone billing plan in USD consistent with the paid amount: what is already collected via PayPal and what to invoice at each milestone."),
]
STAGE = {s[0]: s for s in LIFECYCLE}
TASK_STATUSES = ("todo", "running", "review", "done", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INT NOT NULL, stage TEXT NOT NULL, seq INT, department TEXT, title TEXT,
    assignee_id INT, status TEXT NOT NULL DEFAULT 'todo', output TEXT, output_source TEXT,
    created_at TEXT, updated_at TEXT, completed_at TEXT,
    UNIQUE(project_id, stage)
);
"""


def ensure():
    ops.ensure()
    with conn() as c:
        c.executescript(SCHEMA)
        if "department" not in {r[1] for r in c.execute("pragma table_info(team_members)")}:
            c.execute("alter table team_members add column department TEXT")


# ---------------------------------------------------------------------------
# staffing
# ---------------------------------------------------------------------------

def _dept_members(c, dept):
    return [dict(r) for r in c.execute(
        "select * from team_members where department=? and coalesce(active,1)=1 order by coalesce(kind,'human')='ai_agent', id", (dept,))]

def staff_with_ai(departments: List[str] = None) -> Dict[str, Any]:
    """Create one AI agent for every department that has nobody in it (or for the given departments)."""
    ensure()
    want = departments or [d[0] for d in DEPARTMENTS]
    bad = [d for d in want if d not in DEPT]
    if bad: raise ValueError("unknown department: " + ", ".join(bad))
    created = []
    with conn() as c:
        empty = [d for d in want if not _dept_members(c, d)]
    for d in empty:
        m = ops.add_member({"name": f"{AGENT_NAME[d]} Agent", "kind": "ai_agent",
                            "role": f"{DEPT[d]['name']} AI agent", "skills": d.replace("_", " "), "capacity": 6})
        with conn() as c: c.execute("update team_members set department=? where id=?", (d, m["id"]))
        created.append({"id": m["id"], "name": m["name"], "department": d})
    return {"created": created}

def set_department(member_id: int, dept: str):
    if dept and dept not in DEPT: raise ValueError("unknown department")
    with conn() as c: c.execute("update team_members set department=? where id=?", (dept or None, member_id))


# ---------------------------------------------------------------------------
# lifecycle planning (the Project Manager agent)
# ---------------------------------------------------------------------------

def _pick(c, dept):
    """Least-loaded active member of the department; AI agents and people compete on load."""
    best = None
    for m in _dept_members(c, dept):
        load = c.execute("select count(*) from project_tasks where assignee_id=? and status in ('todo','running','review')", (m["id"],)).fetchone()[0]
        if best is None or load < best[1]: best = (m, load)
    return best[0] if best else None

def plan_project(pid: int) -> Dict[str, Any]:
    """Create one task per lifecycle stage and route it to the owning department. Idempotent."""
    ensure()
    with conn() as c:
        plan_in(c, pid)
    return {"project_id": pid, "tasks": list_tasks(pid)}

def plan_in(c, pid: int):
    """Plan using the caller's connection (safe inside another transaction, e.g. a PayPal settlement)."""
    c.execute(SCHEMA.strip().rstrip(";"))  # single CREATE TABLE IF NOT EXISTS; executescript would commit c's transaction
    p = c.execute("select * from projects where id=?", (pid,)).fetchone()
    if not p: raise LookupError("project not found")
    made = []
    for seq, (stage, dept, title, _) in enumerate(LIFECYCLE, 1):
        if c.execute("select 1 from project_tasks where project_id=? and stage=?", (pid, stage)).fetchone(): continue
        m = _pick(c, dept)
        c.execute("insert into project_tasks(project_id,stage,seq,department,title,assignee_id,status,created_at,updated_at) "
                  "values(?,?,?,?,?,?,'todo',?,?)", (pid, stage, seq, dept, title, m["id"] if m else None, now(), now()))
        made.append(stage)
    pm = _pick(c, "project_management")
    if pm and not p["assignee_id"]:
        c.execute("update projects set assignee_id=?, updated_at=? where id=?", (pm["id"], now(), pid))
    if made:
        ops._log(c, pid, "planned", f"{pm['name'] if pm else 'Project Manager'} planned {len(made)} lifecycle tasks across departments")


def list_tasks(pid: int) -> List[Dict[str, Any]]:
    ensure()
    with conn() as c:
        rows = c.execute("""select t.*, m.name as assignee_name, m.kind as assignee_kind from project_tasks t
                            left join team_members m on m.id=t.assignee_id where t.project_id=? order by t.seq""", (pid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["department_name"] = DEPT.get(d["department"], {}).get("name", d["department"]); out.append(d)
    return out

def _task(c, tid):
    t = c.execute("""select t.*, m.kind as assignee_kind, m.name as assignee_name from project_tasks t
                     left join team_members m on m.id=t.assignee_id where t.id=?""", (tid,)).fetchone()
    if not t: raise LookupError("task not found")
    return dict(t)

def _sync_project(c, pid):
    st = [r[0] for r in c.execute("select status from project_tasks where project_id=?", (pid,))]
    p = c.execute("select status, assignee_id from projects where id=?", (pid,)).fetchone()
    if not st or not p: return
    if p["status"] == "queued" and any(s != "todo" for s in st) and p["assignee_id"]:
        c.execute("update projects set status='in_progress', started_at=coalesce(started_at,?), updated_at=? where id=?", (now(), now(), pid))
        ops._log(c, pid, "status", "queued → in_progress (lifecycle work started)")
    if all(s == "done" for s in st) and p["status"] in ("queued", "in_progress"):
        c.execute("update projects set status='review', updated_at=? where id=?", (now(), pid))
        ops._log(c, pid, "status", "All lifecycle tasks approved → review")


# ---------------------------------------------------------------------------
# department agents at work
# ---------------------------------------------------------------------------

def _context(c, pid):
    p = dict(c.execute("select * from projects where id=?", (pid,)).fetchone())
    lead = c.execute("select * from leads where id=?", (p["lead_id"],)).fetchone() if p["lead_id"] else None
    prop = c.execute("select title, scope_of_work, pricing_tiers from proposals where lead_id=?", (p["lead_id"],)).fetchone() if p["lead_id"] else None
    pay = c.execute("select kind, amount, interval, tier_name, status from payments where id=?", (p["payment_id"],)).fetchone() if p["payment_id"] else None
    return {"project": p["name"], "client": lead["company"] if lead else "Internal", "problem": lead["problem"] if lead else p["description"],
            "service": lead["service"] if lead else None, "skills": p["skills"], "value_usd": p["value"],
            "proposal": prop["title"] if prop else None,
            "scope": json.loads(prop["scope_of_work"]) if prop and prop["scope_of_work"] else None,
            "payment": dict(pay) if pay else None}

def _template(stage, ctx, reason=""):
    title = STAGE[stage][2]
    return (f"**{title}: {ctx['client']}**\n\n- Project: {ctx['project']}\n- Problem: {ctx['problem'] or 'n/a'}\n"
            f"- Value: {'$' + format(int(ctx['value_usd']), ',') if ctx['value_usd'] else 'n/a'}\n\n"
            f"_Template draft: the LLM was unavailable{' (' + reason + ')' if reason else ''}. Re-run the agent for an AI-written deliverable._")

def run_task(tid: int) -> Dict[str, Any]:
    """An AI agent produces its department's deliverable. The task then waits for human approval."""
    ensure()
    with conn() as c:
        t = _task(c, tid)
        if t["assignee_kind"] != "ai_agent": raise ValueError("only tasks owned by an AI agent can be run; people mark their own tasks done")
        if t["status"] not in ("todo", "blocked"): raise ValueError(f"task is already {t['status']}")
        c.execute("update project_tasks set status='running', updated_at=? where id=?", (now(), tid))
        _sync_project(c, t["project_id"])
        ctx = _context(c, t["project_id"])
    stage, dept = t["stage"], DEPT[t["department"]]
    text, source, reason = None, "template", ""
    try:
        import agent
        sysmsg = (f"You are the {dept['name']} AI agent at a software services company. {STAGE[stage][3]} "
                  "Be concrete and specific to this client. Use short markdown bullets with bold section labels, at most 170 words. "
                  "Do not invent prices beyond the provided values. Never claim work was already shipped. "
                  'Return JSON only: {"deliverable": "<markdown>"}')
        # generous budget: reasoning models spend tokens thinking before they answer
        out = agent.llm(sysmsg, json.dumps(ctx, default=str), max_tokens=2000, temperature=0.3)
        body = (out.get("deliverable") or out.get("raw_response")) if isinstance(out, dict) else None
        if body and str(body).strip() and str(body).strip() != "None":
            text, source = str(body).strip()[:6000], "ai"
        else:
            reason = "unexpected model output"
    except Exception as e:
        reason = str(e)[:120]
    if not text: text = _template(stage, ctx, reason)
    with conn() as c:
        c.execute("update project_tasks set status='review', output=?, output_source=?, updated_at=? where id=?", (text, source, now(), tid))
        ops._log(c, t["project_id"], "agent_output", f"{t['assignee_name']} delivered '{t['title']}' for review")
    return get_task(tid)

def run_all(pid: int) -> Dict[str, Any]:
    """Run every to-do task owned by an AI agent in parallel."""
    tids = [t["id"] for t in list_tasks(pid) if t["assignee_kind"] == "ai_agent" and t["status"] in ("todo", "blocked")]
    results, errors = {}, {}
    gate = threading.Semaphore(max(1, int(os.environ.get("AGENT_CONCURRENCY", "3"))))  # stay under provider rate limits
    def go(tid):
        with gate:
            try: results[tid] = run_task(tid)
            except Exception as e: errors[tid] = str(e)
    threads = [threading.Thread(target=go, args=(tid,)) for tid in tids]
    for th in threads: th.start()
    for th in threads: th.join(180)
    return {"ran": len(results), "errors": errors, "tasks": list_tasks(pid)}

def get_task(tid: int) -> Dict[str, Any]:
    with conn() as c:
        t = _task(c, tid)
    t["department_name"] = DEPT.get(t["department"], {}).get("name")
    return t

def update_task(tid: int, b: Dict[str, Any]) -> Dict[str, Any]:
    ensure()
    with conn() as c:
        t = _task(c, tid)
        if "assignee_id" in b:
            aid = b["assignee_id"]
            if aid is not None:
                m = c.execute("select coalesce(active,1) from team_members where id=?", (aid,)).fetchone()
                if not m or not m[0]: raise ValueError("assignee not found or inactive")
            c.execute("update project_tasks set assignee_id=?, updated_at=? where id=?", (aid, now(), tid))
        if "status" in b:
            st = b["status"]
            if st not in ("todo", "done", "blocked"): raise ValueError("status must be todo, done or blocked")
            if st == "done" and t["status"] == "todo" and t["assignee_kind"] == "ai_agent":
                raise ValueError("run the agent first; its output needs review before approval")
            c.execute("update project_tasks set status=?, completed_at=?, updated_at=? where id=?",
                      (st, now() if st == "done" else None, now(), tid))
            ops._log(c, t["project_id"], "task", f"'{t['title']}' → {st}" + (" (approved by admin)" if st == "done" and t["status"] == "review" else ""))
        _sync_project(c, t["project_id"])
    return get_task(tid)


# ---------------------------------------------------------------------------
# company overview + KPIs
# ---------------------------------------------------------------------------

def overview() -> Dict[str, Any]:
    ensure()
    with conn() as c:
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0
        members = [dict(r) for r in c.execute("select id, name, kind, department, role from team_members where coalesce(active,1)=1")]
        tasks = [dict(r) for r in c.execute("select department, status from project_tasks")]
        leads, won = q("select count(*) from leads"), q("select count(*) from leads where status='WON'")
        collected = q("select sum(amount) from payments where status in ('completed','paid')")
        mrr = q("select sum(amount) from payments where status='active'")
        outstanding = q("select sum(amount) from payments where status in ('created','sent')")
        kpi = {
            "sales": [("Leads", leads), ("Meetings", q("select count(*) from leads where status='MEETING'")), ("Won", won),
                      ("Conversion", f"{round(100 * won / leads) if leads else 0}%")],
            "marketing": [("Qualified leads (60+)", q("select count(*) from leads where score>=60"))],
            "presales": [("Proposals", q("select count(*) from proposals"))],
            "finance": [("Collected", f"${collected:,.0f}"), ("PayPal MRR", f"${mrr:,.0f}"), ("Outstanding", f"${outstanding:,.0f}")],
            "project_management": [("Active projects", q("select count(*) from projects where status in ('in_progress','review')")),
                                   ("Queued", q("select count(*) from projects where status='queued'")),
                                   ("Overdue", q("select count(*) from projects where due < date('now') and status!='done'"))],
            "hr": [("People", sum(1 for m in members if (m["kind"] or "human") == "human")),
                   ("AI agents", sum(1 for m in members if m["kind"] == "ai_agent"))],
        }
    depts = []
    for d in DEPARTMENTS:
        did = d[0]
        staff = [m for m in members if m["department"] == did]
        dt = [t for t in tasks if t["department"] == did]
        k = list(kpi.get(did, []))
        if dt: k += [("Tasks done", sum(1 for t in dt if t["status"] == "done")), ("Open", sum(1 for t in dt if t["status"] != "done"))]
        depts.append({**DEPT[did], "pillar_name": PILLARS[DEPT[did]["pillar"]], "people": sum(1 for m in staff if (m["kind"] or "human") == "human"),
                      "agents": sum(1 for m in staff if m["kind"] == "ai_agent"), "staff": [m["name"] for m in staff][:4],
                      "kpis": [{"label": a, "value": b} for a, b in k], "in_lifecycle": any(s[1] == did for s in LIFECYCLE)})
    return {"departments": depts, "pillars": PILLARS, "lifecycle": [{"stage": s[0], "department": s[1], "title": s[2]} for s in LIFECYCLE],
            "unstaffed": [d["id"] for d in depts if not d["people"] and not d["agents"]]}
