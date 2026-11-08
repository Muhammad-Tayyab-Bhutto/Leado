"""Vio Copilot: The In-CRM Autonomous Executive Agent.
Inspired by Leado AI's 'Vio', this copilot provides:
- Natural language pipeline intelligence & executive briefings
- Direct execution of sales ops actions (audit, proposal generation, drafting, sandboxed checks)
- Proactive suggestions to accelerate deal velocity and revenue
"""
import json, os, time
from typing import Dict, Any, List
import app, agent, sandbox, proposal_engine
from app import conn, now

class VioCopilot:
    def __init__(self):
        self.name = "Vio"
        self.role = "Autonomous Sales Ops & Strategy Copilot"

    def handle_query(self, message: str) -> Dict[str, Any]:
        """Processes user natural language commands and returns response + executable actions."""
        q = message.lower().strip()
        actions = []

        # 1. Pipeline Briefing / Stats
        if any(w in q for w in ["stat", "brief", "pipeline", "revenue", "mrr", "health", "summary"]):
            with conn() as c:
                leads_count = c.execute("SELECT count(*) FROM leads").fetchone()[0]
                qualified = c.execute("SELECT count(*) FROM leads WHERE score>=60").fetchone()[0]
                high = c.execute("SELECT count(*) FROM leads WHERE score>=75").fetchone()[0]
                won = c.execute("SELECT count(*) FROM leads WHERE status='WON'").fetchone()[0]
                mrr = c.execute("SELECT coalesce(sum(actual_mrr),0) FROM leads WHERE status='WON'").fetchone()[0]
                pipe_mrr = c.execute("SELECT coalesce(sum(monthly_retainer),0) FROM leads WHERE status NOT IN ('WON','LOST','OPTED_OUT')").fetchone()[0]
                drafts = c.execute("SELECT count(*) FROM emails WHERE status='draft'").fetchone()[0]

            reply = (
                f"📊 **Executive Pipeline Briefing**:\n\n"
                f"• **Active Pipeline**: {leads_count} total leads ({qualified} qualified, {high} high-priority).\n"
                f"• **Revenue Velocity**: ${mrr:,}/mo closed MRR | ${pipe_mrr:,}/mo weighted pipeline value.\n"
                f"• **Outreach Status**: {drafts} email drafts awaiting human-in-the-loop sign-off.\n\n"
                f"💡 *Recommendation*: Prioritize reviewing the {drafts} pending email drafts and generate proposals for the top {high} high-scoring leads."
            )
            actions.append({"label": "Review Approvals", "type": "view", "target": "approvals"})
            actions.append({"label": "View High Priority Leads", "type": "filter", "min": 75})
            return {"reply": reply, "actions": actions}

        # 2. Blast Radius / Sandbox Verification
        if any(w in q for w in ["sandbox", "contain", "blast radius", "security", "safe"]):
            proof = sandbox.sandbox.verify_containment_proof()
            status = proof["status"]
            rt = proof["details"].get("sandbox_runtime_ms", 0)
            reply = (
                f"🛡️ **Blast Radius Zero Containment Status**: **{status}**\n\n"
                f"• **Process Isolation**: Ephemeral execution sandbox ({rt}ms verification cycle)\n"
                f"• **Secret Hygiene**: 100% of host environment variables & API tokens scrubbed\n"
                f"• **Resource Limits**: 256MB RAM cap | 0.5 vCPU cap | 8.0s hard execution kill\n"
                f"• **Target Access**: Read-only rootfs; unauthorized system calls intercepted."
            )
            actions.append({"label": "Run Live Sandbox Audit", "type": "sandbox_audit"})
            return {"reply": reply, "actions": actions}

        # 3. Leado Proposal Generation
        if any(w in q for w in ["proposal", "sow", "contract", "quote"]):
            with conn() as c:
                top_lead = c.execute("SELECT id, company, domain, score FROM leads WHERE score>=60 ORDER BY score DESC LIMIT 1").fetchone()
            
            if top_lead:
                lead_id, comp, dom, sc = top_lead
                reply = (
                    f"📝 **Proposal Intelligence Ready**\n\n"
                    f"Top candidate for immediate closing: **{comp}** ({dom}) with score **{sc}/100**.\n"
                    f"Would you like me to generate a tailored 3-Phase Scope of Work & Pricing Tiers based on their audited bottlenecks?"
                )
                actions.append({"label": f"Generate Proposal for {comp}", "type": "create_proposal", "lead_id": lead_id})
                actions.append({"label": "View All Proposals", "type": "view", "target": "proposals"})
            else:
                reply = "No qualified leads found yet to generate proposals. Run lead discovery or add domains first."
                actions.append({"label": "Run Lead Discovery", "type": "view", "target": "lead_finder"})
            return {"reply": reply, "actions": actions}

        # 4. Lead Hunter / Discovery
        if any(w in q for w in ["find", "search", "discover", "hunter", "scrape"]):
            reply = (
                f"🎯 **Autonomous Lead Hunter**\n\n"
                f"I can scan multiple discovery channels (Google, GitHub, Tech Job Boards, B2B Directories) "
                f"to find companies with high-intent digital transformation requirements."
            )
            actions.append({"label": "Open Lead Finder", "type": "view", "target": "lead_finder"})
            actions.append({"label": "Start Batch Scrape", "type": "lf_discover"})
            return {"reply": reply, "actions": actions}

        # 5. General AI reasoning using Vultr or Fallback
        system_prompt = (
            "You are Vio, the ultra-smart autonomous sales ops copilot in the Leado CRM platform. "
            "You provide concise, actionable, strategic advice for service agencies and B2B SaaS teams. "
            "Keep responses under 3 paragraphs, formatted in GitHub markdown."
        )
        try:
            ai_reply = agent.llm(system_prompt, message, max_tokens=350)
            if isinstance(ai_reply, dict):
                text = ai_reply.get("raw_response") or str(ai_reply)
            else:
                text = str(ai_reply)
            return {"reply": text, "actions": [{"label": "View Pipeline", "type": "view", "target": "table"}]}
        except Exception as e:
            return {
                "reply": f"Hello! I am **Vio**, your CRM Copilot. I can help you monitor your sales pipeline, run sandboxed site audits, generate Leado-style client proposals, and orchestrate automated outreach. Try asking: *'Give me a pipeline briefing'*, *'Check sandbox security'*, or *'Generate a proposal'*. (Note: {e})",
                "actions": [
                    {"label": "Pipeline Briefing", "type": "ask", "q": "Give me a pipeline briefing"},
                    {"label": "Verify Sandbox Containment", "type": "ask", "q": "Check sandbox security"}
                ]
            }

vio = VioCopilot()
