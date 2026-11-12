"""Proposal Intelligence Engine.
Generates comprehensive Statements of Work (SOW) and commercial proposals
tailored directly to audited client evidence and technical gaps.
"""
import json, os, time
from typing import Dict, Any, Optional
import app
from app import conn, now

PROPOSAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    executive_summary TEXT,
    audit_findings TEXT,
    scope_of_work TEXT,
    deliverables TEXT,
    pricing_tiers TEXT,
    timeline_weeks INT DEFAULT 4,
    est_value INT DEFAULT 3500,
    status TEXT DEFAULT 'draft',
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(lead_id) REFERENCES leads(id)
);
"""

def init_proposals():
    with conn() as c:
        c.executescript(PROPOSAL_SCHEMA)

def generate_proposal(lead_id: int) -> Dict[str, Any]:
    """Generates an executive proposal for the given lead based on researched evidence."""
    init_proposals()
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not r:
            raise LookupError(f"Lead {lead_id} not found")
        lead = dict(r)

    company = lead.get("company") or "Target Company"
    domain = lead.get("domain") or lead.get("website") or "company.com"
    service = lead.get("service") or "web_app"
    problem = lead.get("problem") or "Digital workflow inefficiencies and manual operational overhead"
    evidence_url = lead.get("evidence_url") or lead.get("website") or ""
    package = lead.get("package") or "C"
    contact = lead.get("contact_name") or f"Leadership at {company}"

    title = f"Growth & Systems Modernization Proposal — {company}"
    
    # Try generating high-level analysis via LLM if available
    llm_summary = None
    try:
        import agent
        if os.environ.get("VULTR_INFERENCE_API_KEY") or os.environ.get("OPENROUTER_API_KEY"):
            prompt = (
                f"You are a principal technical consultant. Write a 2-paragraph executive proposal summary for {company} ({domain}). "
                f"They have an identified operational bottleneck: {problem}. "
                f"Proposed service: {service}. Tone: strategic, ROI-driven, founder-to-founder."
            )
            raw = agent.llm("You generate executive consulting proposals. Return plain text.", prompt, max_tokens=300)
            if isinstance(raw, str) and len(raw) > 50:
                llm_summary = raw
    except Exception:
        pass

    if not llm_summary:
        llm_summary = (
            f"{company} has established solid market presence, yet current digital operations at {domain} "
            f"show actionable friction in {service.replace('_', ' ')}. Our diagnostic assessment identified: '{problem}', "
            f"which limits team throughput and customer conversion velocity.\n\n"
            f"This proposal outlines a phased modernization roadmap designed to eliminate operational friction, "
            f"automate repetitive tasks, and deploy scalable systems without inflating internal headcount."
        )

    audit_findings = [
        f"Diagnosed Bottleneck: {problem}",
        f"Verified Source: {evidence_url}",
        f"Current Service Alignment: {service.replace('_', ' ').title()}",
        f"Lead Score Index: {lead.get('score', 75)}/100 ({lead.get('score_reason', 'Qualified')})",
        f"Target Contact: {contact} ({lead.get('contact_title') or 'Decision Maker'})"
    ]

    scope_of_work = [
        {
            "phase": "Phase 1: Architecture Audit & Core Remediation",
            "duration": "Weeks 1–2",
            "tasks": [
                f"Perform comprehensive system deep-dive on {domain}",
                f"Resolve primary friction point: {problem}",
                "Establish baseline telemetry, error monitoring, and conversion tracking"
            ]
        },
        {
            "phase": "Phase 2: Workflow Automation & Intelligence Layer",
            "duration": "Weeks 3–4",
            "tasks": [
                "Implement automated agent pipelines to eliminate manual data entry",
                "Integrate CRM/ERP synchronization hooks for real-time lead ingestion",
                "Deploy human-in-the-loop review gates for automated customer communication"
            ]
        },
        {
            "phase": "Phase 3: Scale, Security & Ongoing Optimization",
            "duration": "Ongoing Monthly Retainer",
            "tasks": [
                "24/7 continuous health & performance monitoring",
                "Bi-weekly capability updates and prompt engineering calibration",
                "Executive monthly ROI review & strategic optimization"
            ]
        }
    ]

    deliverables = [
        "Fully documented System Architecture & Audit Report",
        f"Production-deployed {service.replace('_', ' ')} solution with containment isolation",
        "Automated operational workflows & API connectors",
        "Executive Dashboard & Reporting Suite",
        "Standard Operating Procedures (SOP) & Team Training"
    ]

    base_low = lead.get("est_mrr_low") or 1500
    base_high = lead.get("est_mrr_high") or 4000

    pricing_tiers = [
        {
            "name": "Sprint Remediation",
            "price": f"${base_low * 2:,} one-time", "amount": base_low * 2, "interval": "once",
            "highlight": "Essential fix & system hardening",
            "features": [
                "Immediate resolution of diagnosed bottlenecks",
                "Baseline security & speed audit",
                "14-day warranty and deployment sign-off"
            ]
        },
        {
            "name": "Growth Partner (Recommended)",
            "price": f"${base_high:,}/month", "amount": base_high, "interval": "month",
            "highlight": "Full AI Operations & Continuous Growth",
            "features": [
                "Complete Phase 1 & Phase 2 execution included",
                "Dedicated autonomous AI agents & sandboxed execution",
                "Priority 24/7 SLA & founder-level advisory",
                "Quarterly strategy roadmap"
            ]
        },
        {
            "name": "Enterprise Autonomous Suite",
            "price": f"${base_high * 2:,}/month", "amount": base_high * 2, "interval": "month",
            "highlight": "Custom multi-agent workforce",
            "features": [
                "Custom LLM fine-tuning & private cloud hosting",
                "Unlimited workflow automations",
                "White-glove SLA with 1-hour response time"
            ]
        }
    ]

    proposal_data = {
        "lead_id": lead_id,
        "title": title,
        "executive_summary": llm_summary,
        "audit_findings": json.dumps(audit_findings),
        "scope_of_work": json.dumps(scope_of_work),
        "deliverables": json.dumps(deliverables),
        "pricing_tiers": json.dumps(pricing_tiers),
        "timeline_weeks": 4,
        "est_value": base_high,
        "status": "draft",
        "updated_at": now()
    }

    with conn() as c:
        existing = c.execute("SELECT id FROM proposals WHERE lead_id=?", (lead_id,)).fetchone()
        if existing:
            c.execute("""
                UPDATE proposals SET 
                    title=?, executive_summary=?, audit_findings=?, scope_of_work=?, 
                    deliverables=?, pricing_tiers=?, timeline_weeks=?, est_value=?, 
                    status='draft', updated_at=?
                WHERE lead_id=?
            """, (
                proposal_data["title"], proposal_data["executive_summary"], proposal_data["audit_findings"],
                proposal_data["scope_of_work"], proposal_data["deliverables"], proposal_data["pricing_tiers"],
                proposal_data["timeline_weeks"], proposal_data["est_value"], proposal_data["updated_at"],
                lead_id
            ))
            proposal_id = existing[0]
        else:
            c.execute("""
                INSERT INTO proposals (
                    lead_id, title, executive_summary, audit_findings, scope_of_work,
                    deliverables, pricing_tiers, timeline_weeks, est_value, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead_id, proposal_data["title"], proposal_data["executive_summary"], proposal_data["audit_findings"],
                proposal_data["scope_of_work"], proposal_data["deliverables"], proposal_data["pricing_tiers"],
                proposal_data["timeline_weeks"], proposal_data["est_value"], "draft", now(), now()
            ))
            proposal_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    return get_proposal(lead_id)

def get_proposal(lead_id: int) -> Optional[Dict[str, Any]]:
    init_proposals()
    with conn() as c:
        r = c.execute("SELECT * FROM proposals WHERE lead_id=?", (lead_id,)).fetchone()
        if not r: return None
        d = dict(r)
        for field in ("audit_findings", "scope_of_work", "deliverables", "pricing_tiers"):
            if d.get(field):
                try: d[field] = json.loads(d[field])
                except Exception: pass
        return d

def list_proposals() -> list:
    init_proposals()
    with conn() as c:
        rows = c.execute("""
            SELECT p.*, l.company, l.website, l.contact_name, l.contact_email, l.score 
            FROM proposals p 
            JOIN leads l ON p.lead_id = l.id 
            ORDER BY p.updated_at DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for field in ("audit_findings", "scope_of_work", "deliverables", "pricing_tiers"):
                if d.get(field):
                    try: d[field] = json.loads(d[field])
                    except Exception: pass
            result.append(d)
        return result
