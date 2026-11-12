"""Seed rich, realistic demo pipeline data for Dealwright.
Populates leads across stages (SaaS, FinTech, HealthTech, DevTools, E-commerce),
with verified evidence, scores, proposals, email drafts, and activity events.
"""
import json, sqlite3, os
from datetime import datetime, timezone
import app, proposal_engine

now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

DEMO_LEADS = [
    {
        "domain": "datastream-analytics.io",
        "company": "DataStream Analytics",
        "website": "https://datastream-analytics.io",
        "industry": "technology",
        "country": "United States",
        "city": "San Francisco",
        "service": "ai_agents",
        "problem": "Manual CSV export workflows causing 4-hour delay in real-time reporting to client dashboards",
        "evidence_url": "https://datastream-analytics.io/pricing",
        "contact_name": "Sarah Chen",
        "contact_title": "VP of Engineering",
        "contact_email": "sarah.chen@datastream-analytics.io",
        "email_status": "verified",
        "email_source": "hunter.io",
        "f_relevance": 95,
        "f_problem": 90,
        "f_pay": 85,
        "f_recurring": 90,
        "f_access": 80,
        "f_growth": 85,
        "f_data": 90,
        "status": "PROPOSAL",
        "monthly_retainer": 4500,
        "est_project_value": 15000,
        "notes": "Met at SaaS Summit. Eager to automate data ingestion with sandboxed agents."
    },
    {
        "domain": "finflow-payments.com",
        "company": "FinFlow Payments",
        "website": "https://finflow-payments.com",
        "industry": "finance",
        "country": "United Kingdom",
        "city": "London",
        "service": "automation",
        "problem": "Legacy webhook dispatch failures leading to un-reconciled merchant transactions",
        "evidence_url": "https://finflow-payments.com/developers",
        "contact_name": "Marcus Vance",
        "contact_title": "Head of Operations",
        "contact_email": "marcus.v@finflow-payments.com",
        "email_status": "verified",
        "email_source": "clearbit",
        "f_relevance": 90,
        "f_problem": 85,
        "f_pay": 95,
        "f_recurring": 85,
        "f_access": 75,
        "f_growth": 90,
        "f_data": 85,
        "status": "MEETING",
        "monthly_retainer": 6000,
        "est_project_value": 22000,
        "notes": "Intro meeting scheduled for Thursday. Need automated reconciliation worker."
    },
    {
        "domain": "cloudpulse-infra.net",
        "company": "CloudPulse Systems",
        "website": "https://cloudpulse-infra.net",
        "industry": "technology",
        "country": "Germany",
        "city": "Berlin",
        "service": "devops",
        "problem": "Un-isolated build environments risking host credential leakage during customer code evaluation",
        "evidence_url": "https://cloudpulse-infra.net/security",
        "contact_name": "Lukas Weber",
        "contact_title": "Chief Technology Officer",
        "contact_email": "lukas@cloudpulse-infra.net",
        "email_status": "verified",
        "email_source": "github",
        "f_relevance": 100,
        "f_problem": 95,
        "f_pay": 90,
        "f_recurring": 95,
        "f_access": 90,
        "f_growth": 90,
        "f_data": 90,
        "status": "QUALIFIED",
        "monthly_retainer": 5000,
        "est_project_value": 18000,
        "notes": "Perfect candidate for Blast Radius Zero sandboxed execution architecture."
    },
    {
        "domain": "healthnexus-ehr.com",
        "company": "HealthNexus Solutions",
        "website": "https://healthnexus-ehr.com",
        "industry": "healthcare",
        "country": "United States",
        "city": "Boston",
        "service": "saas",
        "problem": "Outdated patient intake portal resulting in 22% abandonment rate on mobile devices",
        "evidence_url": "https://healthnexus-ehr.com/patients",
        "contact_name": "Dr. Elena Rostova",
        "contact_title": "Director of Digital Health",
        "contact_email": "erostova@healthnexus-ehr.com",
        "email_status": "verified",
        "email_source": "directory",
        "f_relevance": 85,
        "f_problem": 80,
        "f_pay": 90,
        "f_recurring": 80,
        "f_access": 70,
        "f_growth": 75,
        "f_data": 85,
        "status": "EMAIL_DRAFTED",
        "monthly_retainer": 3500,
        "est_project_value": 12000,
        "notes": "Intake portal needs responsive redesign and HIPAA-compliant automation."
    },
    {
        "domain": "apex-logistics.ai",
        "company": "Apex Freight Logistics",
        "website": "https://apex-logistics.ai",
        "industry": "logistics",
        "country": "Canada",
        "city": "Toronto",
        "service": "erp_crm",
        "problem": "Manual rate card lookup and email quoting taking 45 minutes per dispatch order",
        "evidence_url": "https://apex-logistics.ai/contact",
        "contact_name": "David Tremblay",
        "contact_title": "VP Supply Chain",
        "contact_email": "d.tremblay@apex-logistics.ai",
        "email_status": "verified",
        "email_source": "web_scrape",
        "f_relevance": 90,
        "f_problem": 90,
        "f_pay": 85,
        "f_recurring": 90,
        "f_access": 80,
        "f_growth": 85,
        "f_data": 85,
        "status": "WON",
        "actual_mrr": 4500,
        "monthly_retainer": 4500,
        "est_project_value": 20000,
        "client_status": "active",
        "contract_start": "2026-11-01",
        "acquired_at": "2026-11-01",
        "notes": "Closed on annual retainer. Vultr VM hosting active."
    }
]

def seed():
    app.init()
    proposal_engine.init_proposals()
    
    for l in DEMO_LEADS:
        try:
            app.upsert(l)
        except Exception as e:
            print(f"Error inserting {l['company']}: {e}")
            
    # Add drafts and proposals
    with app.conn() as c:
        rows = c.execute("SELECT id, domain, company, score FROM leads WHERE domain != 'softorasystems.com'").fetchall()
        lead_list = [(r[0], r[1], r[2], r[3]) for r in rows]
        
    for lead_id, domain, company, score in lead_list:
        # Generate proposal
        try:
            p = proposal_engine.get_proposal(lead_id)
            stale = p and p.get("status") != "paid" and ("Vultr" in json.dumps(p.get("pricing_tiers")) or
                                                          not all("amount" in t for t in p.get("pricing_tiers") or []))
            if not p or stale:  # idempotent: keeps pay links/paid proposals, refreshes outdated ones
                proposal_engine.generate_proposal(lead_id)
        except Exception as ex:
            print(f"Proposal gen error for {company}: {ex}")
            
        with app.conn() as c:
            existing_email = c.execute("SELECT id FROM emails WHERE lead_id=?", (lead_id,)).fetchone()
            if not existing_email:
                c.execute("""
                    INSERT INTO emails (lead_id, approach, step, subject, body, status, due_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    lead_id,
                    "A (Direct Value)",
                    0,
                    f"Quick diagnostic note on {company}'s digital workflow",
                    f"Hi {company} team,\n\nWhile reviewing {domain}, our automated crawler identified an operational bottleneck: {DEMO_LEADS[0]['problem']}.\n\nDealwright can scope a fix, quote it, and take payment through PayPal in a single flow.\n\nWorth a brief 10-minute briefing?\n\nBest,\nMuhammad Tayyab Bhutto\nFounder, Softora Systems",
                    "draft",
                    "2026-11-10"
                ))
                
            c.execute("""
                INSERT INTO events (lead_id, kind, detail, at)
                VALUES (?, ?, ?, ?)
            """, (
                lead_id,
                "discovery",
                f"Discovered via autonomous multi-source scraper with score {score}/100",
                now()
            ))

    print("Pipeline seeded with 5 enterprise leads, proposals, and drafts!")

if __name__ == "__main__":
    seed()
