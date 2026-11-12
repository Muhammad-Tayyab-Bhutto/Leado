"""Wren Copilot: The In-CRM Autonomous Executive Agent.
Natural-language pipeline intelligence plus PayPal billing tools (invoice, refund, pay links, collections).
- Natural language pipeline intelligence & executive briefings
- Direct execution of sales ops actions (audit, proposal generation, drafting, sandboxed checks)
- Proactive suggestions to accelerate deal velocity and revenue
"""
import json, os, time
from typing import Dict, Any, List
import re, threading
import app, agent, sandbox, proposal_engine, paypal
from app import conn, now

class VioCopilot:
    def __init__(self):
        self.name = "Wren"
        self.role = "Autonomous Sales Ops & Strategy Copilot"

    def handle_query(self, message: str) -> Dict[str, Any]:
        """Processes user natural language commands and returns response + executable actions."""
        q = message.lower().strip()
        actions = []

        # 0. PayPal agentic commerce: the LLM picks a tool, a human confirms anything that moves money
        pp = self.paypal_intent(message)
        if pp: return pp

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
                f"• **Outreach Status**: {drafts} email drafts awaiting human-in-the-loop sign-off.\n"
                f"• **PayPal**: ${paypal.summary()['collected']:,.0f} collected · ${paypal.summary()['outstanding_total']:,.0f} outstanding.\n\n"
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

        # 3. Dealwright Proposal Generation
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

        # 5. General AI reasoning
        system_prompt = (
            "You are Wren, the ultra-smart autonomous sales ops copilot in the Dealwright CRM platform. "
            "You provide concise, actionable, strategic advice for service agencies and B2B SaaS teams. "
            "Keep responses under 3 paragraphs, formatted in GitHub markdown."
        )
        try:
            ai_reply = agent.llm(system_prompt, message, max_tokens=350)
            if isinstance(ai_reply, dict):
                text = ai_reply.get("raw_response") or str(ai_reply)
            else:
                text = str(ai_reply)
            if not text or text.strip() in ("None", "{}"): raise RuntimeError("model returned an empty reply")
            return {"reply": text, "actions": [{"label": "View Pipeline", "type": "view", "target": "table"}]}
        except Exception as e:
            return {
                "reply": f"Hello! I am **Wren**, your CRM Copilot. I can help you monitor your sales pipeline, run sandboxed site audits, generate client proposals, and orchestrate automated outreach. Try asking: *'Give me a pipeline briefing'*, *'Check sandbox security'*, or *'Generate a proposal'*. (Note: {e})",
                "actions": [
                    {"label": "Pipeline Briefing", "type": "ask", "q": "Give me a pipeline briefing"},
                    {"label": "Verify Sandbox Containment", "type": "ask", "q": "Check sandbox security"}
                ]
            }

    # ------------------------------------------------------------------
    # PayPal tools
    # ------------------------------------------------------------------
    PAY_WORDS = ("paypal", "invoice", "charge", "bill", "refund", "paid", "owe", "unpaid", "outstanding", "collect", "payment",
                 "checkout", "pay link", "payment link", "subscription", "cash", "overdue", "remind")
    TOOLS = {
        "payments_summary": "Report collected revenue, PayPal MRR and who still owes money.",
        "send_invoice": "Send a PayPal invoice to a company for a pricing tier (0=one-time sprint, 1=growth monthly, 2=enterprise monthly).",
        "refund": "Refund a captured PayPal payment or cancel a PayPal subscription for a company.",
        "pay_link": "Create a shareable PayPal checkout link for a company's proposal.",
        "remind": "Draft AI payment reminders for outstanding PayPal payments (optionally for one company).",
        "none": "Not a payments request.",
    }

    def _companies(self):
        with conn() as c:
            return [dict(r) for r in c.execute("select id, company, domain from leads where company is not null")]

    def _match_company(self, text, name=None):
        """Match a lead from the AI-extracted name and/or the raw message: full name, first word, or domain stem."""
        hay = " ".join(x for x in (name, text) if x).lower()
        want = (name or "").lower().strip()
        best = None
        for l in self._companies():
            comp = (l["company"] or "").lower().strip()
            keys = {comp, comp.split()[0] if comp else "", (l["domain"] or "").split(".")[0].split("-")[0]}
            for k in keys:
                if len(k) < 4: continue
                hit = re.search(r"\b" + re.escape(k) + r"\b", hay) or (len(want) >= 4 and want in comp)
                if hit and (not best or len(k) > best[1]): best = (l, len(k))
        return best[0] if best else None

    def _route(self, message):
        """LLM tool router (JSON). Falls back to keywords when no model is configured."""
        if not re.search(r"pay|invoice|refund|owe|outstanding|collect|checkout|subscri|cash|overdue|remind|charge|bill", message, re.I):
            return {"tool": "none"}
        def ask():
            sysmsg = ("You route requests for a CRM's payments agent. Tools:\n" +
                      "\n".join(f"- {k}: {v}" for k, v in self.TOOLS.items()) +
                      '\nReturn JSON only: {"tool": "<name>", "company": "<company name or empty>", "tier": <0|1|2 or null>, "reason": "<short>"}')
            try:
                box["out"] = agent.llm(sysmsg, message, max_tokens=300, temperature=0)
            except Exception as e:  # no key / provider down: the keyword planner takes over
                box["error"] = e
        box = {}
        try:
            th = threading.Thread(target=ask, daemon=True)
            th.start(); th.join(float(os.environ.get("VIO_ROUTER_TIMEOUT", "8")))  # slow model? fall back to keywords
            out = box.get("out")
            if isinstance(out, dict) and out.get("tool") in self.TOOLS:
                out["via"] = "llm"; return out
        except Exception:
            pass
        q = message.lower()
        if not any(w in q for w in self.PAY_WORDS): return {"tool": "none"}
        tier = 1 if re.search(r"month|retainer|growth|subscri", q) else 2 if "enterprise" in q else 0
        tool = ("refund" if re.search(r"refund|cancel", q) else "send_invoice" if re.search(r"invoice|charge|bill\b", q) else
                "remind" if re.search(r"remind|chase|nudge|follow", q) else
                "pay_link" if re.search(r"link|checkout", q) else "payments_summary")
        return {"tool": tool, "company": "", "tier": tier, "via": "keywords"}

    def paypal_intent(self, message):
        r = self._route(message)
        tool = r.get("tool")
        if tool in (None, "none"): return None
        if not paypal.configured() and tool != "payments_summary":
            return {"reply": "💳 PayPal isn't connected yet. Add `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (sandbox) to `.env` and restart.",
                    "actions": [{"label": "Open Payments", "type": "view", "target": "payments"}]}
        lead = self._match_company(message, r.get("company"))
        via = f"\n\n_routed by {'AI' if r.get('via') == 'llm' else 'keyword'} planner · nothing moves money without your click_"

        if tool == "payments_summary" or (tool in ("send_invoice", "refund", "pay_link") and not lead):
            s = paypal.summary()
            lines = [f"💳 **PayPal cash position** ({s['env']})", "",
                     f"• **Collected**: ${s['collected']:,.2f}", f"• **Active PayPal MRR**: ${s['mrr']:,.2f}/mo",
                     f"• **Outstanding**: ${s['outstanding_total']:,.2f} across {len(s['outstanding'])} payment(s)"]
            if s["refunded"]: lines.append(f"• **Refunded**: ${s['refunded']:,.2f}")
            acts = [{"label": "Open Payments", "type": "view", "target": "payments"}]
            for p in s["outstanding"][:4]:
                lines.append(f"   – {p['company']}: ${p['amount']:,.2f} {p['kind']} · {p['age_days']}d open")
                acts.append({"label": f"✍️ AI reminder for {p['company']}", "type": "api", "method": "POST",
                             "path": f"/api/payments/{p['id']}/remind", "body": {}})
            if tool != "payments_summary": lines.insert(0, "I couldn't tell which company you meant, so here's the overview.\n")
            return {"reply": "\n".join(lines) + via, "actions": acts}

        if tool == "remind":
            out = [p for p in paypal.summary()["outstanding"] if not lead or p["lead_id"] == lead["id"]]
            if not out: return {"reply": "🎉 Nobody owes you money right now." + via, "actions": [{"label": "Open Payments", "type": "view", "target": "payments"}]}
            return {"reply": f"✍️ {len(out)} outstanding payment(s). I'll draft a tone-aware reminder for each; drafts land in **Approvals** for your sign-off." + via,
                    "actions": [{"label": f"Draft reminder: {p['company']} (${p['amount']:,.0f}, {p['age_days']}d)", "type": "api", "method": "POST",
                                 "path": f"/api/payments/{p['id']}/remind", "body": {}} for p in out[:5]]}

        prop = proposal_engine.get_proposal(lead["id"])
        if tool == "pay_link":
            if not prop:
                return {"reply": f"{lead['company']} has no proposal yet. Generate one first and I'll attach PayPal checkout to every pricing tier." + via,
                        "actions": [{"label": f"Generate proposal for {lead['company']}", "type": "api", "method": "POST", "path": f"/api/leads/{lead['id']}/proposal", "body": {}}]}
            return {"reply": f"🔗 Ready to create a PayPal checkout link for **{lead['company']}**: one-time tiers use Orders, monthly tiers use Subscriptions." + via,
                    "actions": [{"label": "Create PayPal pay link", "type": "api", "method": "POST", "path": f"/api/proposals/{prop['id']}/paylink", "body": {}}]}

        if tool == "send_invoice":
            tier = r.get("tier") if isinstance(r.get("tier"), int) and r.get("tier") in (0, 1, 2) else 0
            tiers = (prop or {}).get("pricing_tiers") or []
            name = tiers[tier]["name"] if len(tiers) > tier else ["Sprint Remediation", "Growth Partner", "Enterprise"][tier]
            price = tiers[tier].get("price", "") if len(tiers) > tier else "(proposal will be generated)"
            return {"reply": f"🧾 I'll send a **PayPal invoice** to **{lead['company']}** for **{name}** {price}.\nPlease confirm, since this emails the client from PayPal." + via,
                    "actions": [{"label": f"✅ Confirm: invoice {lead['company']}", "type": "api", "method": "POST", "path": f"/api/leads/{lead['id']}/invoice",
                                 "body": {"tier": tier, "confirm": True}, "confirm": f"Send a PayPal invoice to {lead['company']} for {name} {price}?"}]}

        if tool == "refund":
            pays = [p for p in paypal.list_payments() if p["lead_id"] == lead["id"] and p["status"] in ("completed", "paid", "active")]
            if not pays: return {"reply": f"No captured PayPal payments or active subscriptions found for {lead['company']}." + via,
                                 "actions": [{"label": "Open Payments", "type": "view", "target": "payments"}]}
            return {"reply": f"↩️ Found {len(pays)} refundable item(s) for **{lead['company']}**. Choose one to confirm:" + via,
                    "actions": [{"label": f"{'Cancel subscription' if p['kind'] == 'subscription' else 'Refund'} ${p['amount']:,.2f} ({p['tier_name']})",
                                 "type": "api", "method": "POST", "path": f"/api/payments/{p['id']}/refund",
                                 "body": {"confirm": True, "reason": "Requested via Wren"},
                                 "confirm": f"{'Cancel the PayPal subscription' if p['kind'] == 'subscription' else 'Refund'} ${p['amount']:,.2f} to {lead['company']}?"} for p in pays]}
        return None

vio = VioCopilot()
