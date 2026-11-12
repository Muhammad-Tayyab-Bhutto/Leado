# Demo video script (target 2:45, hard limit 3:00)

**Setup before recording**
- `python scripts/paypal_selfcheck.py` must show all PASS. Then run `python seed_demo_pipeline.py` and `python app.py serve 8000`, with a *fast* LLM model in `.env`.
- Tab 1: the CRM (signed in). Tab 2: a private window for the client and the PayPal sandbox **Personal** account.
- Edit one demo lead's contact email to your sandbox Personal email, so the invoice arrives in that account.
- Do a full dry run first: the first PayPal call of a session is slower (OAuth token).
- No background music unless it's royalty-free. Record at 1080p.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Pipeline view | "Agencies lose deals between *proposal sent* and *got paid*. Dealwright is an AI agent that closes that gap, from first research to money in PayPal." |
| 0:15–0:45 | Open **FinFlow** → the problem found in the audit → **⚡ Proposal** → **View Full SOW** | "The AI found a real bottleneck on their site and turned it into a scoped proposal with three pricing tiers: a one-time sprint and two monthly plans." |
| 0:45–1:00 | **💳 PayPal checkout link** → paste it into the private window | "One click gives the client a pay page. One-time tiers use PayPal Orders; monthly tiers create a PayPal subscription on the fly." |
| 1:00–1:35 | Client page → **Subscribe with PayPal** on Growth → sandbox login → approve → confirmation | "The client approves in PayPal…" (pause on the confirmation) "…and the subscription is live." |
| 1:35–1:50 | Back to the CRM: lead is **WON** → **PayPal Payments** (AG Grid ledger): filter by status, sort by age | "No manual data entry. The capture, or a signature-verified webhook, moved the deal to Won and recorded the MRR. The ledger is AG Grid: filter what's outstanding, sort by age, act inline." |
| 1:50–2:25 | **Wren** → *"Who still owes us money on PayPal?"* → **AI reminder** → *"Invoice CloudPulse for the one-time sprint"* → the confirm dialog → the invoice ID | "Wren is the agent. An LLM picks the PayPal tool, finds the company and the tier, and drafts collection emails whose tone depends on how long they've been unpaid. It never moves money by itself: every invoice, refund or cancellation needs my click." |
| 2:25–2:40 | **Approvals** shows the reminder draft · **PayPal Payments** → **Refund** on the sprint | "Reminders go through human approval, and refunds are one confirmed click." |
| 2:40–2:55 | README architecture diagram | "PayPal Orders, Subscriptions, Invoicing, Refunds and Webhooks, driven by AI with a human in the loop. That's Dealwright." |
