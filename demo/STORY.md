## Inspiration

I run a small software house in Pakistan. Our hardest problem was never writing code. It was everything around it: finding the next client, writing the proposal, chasing the payment, and then turning a paid deal into a plan that design, engineering, QA and DevOps can act on. Each handoff lived in a different tool, and every one was a place where deals stalled and money went uncollected.

A software company is really a pipeline: **lead → proposal → payment → delivery**. Payments sit in the middle, so we asked: what if PayPal were the moment that sets the whole company in motion, and AI agents did the busywork on both sides of it, with a person approving everything that matters?

## What it does

Dealwright runs a software house end to end, with people and AI agents working side by side.

**Revenue**
- Sales agents research prospects and quote evidence from the prospect's own website.
- A proposal agent writes a scoped proposal with three pricing tiers.
- One click turns the proposal into a client pay page: one-time tiers use **PayPal Orders v2**, and monthly retainers create a **PayPal product, plan and subscription** on the fly.
- When PayPal confirms the payment (on the return URL or a signature-verified **webhook**), the deal moves to *Won* and revenue is recorded, with no manual data entry.

**Delivery**
- The paid deal becomes a project, and a **Project Manager agent** plans a 9-stage lifecycle: requirements and estimate, sprint plan, UX, architecture, test plan, security review, deployment runbook, handover, and milestone billing.
- Each stage goes to the department that owns it. **Department AI agents** write their deliverable for that specific client, and every deliverable waits for human approval. Tasks owned by people stay with them.

**The company**
- **20 departments** in four pillars (Revenue, Delivery, Operations, Product & R&D), staffed by people, AI agents, or both, with live KPIs.
- Admins add employees and agents, see who is free, busy or at capacity and what they are working on, and auto-assign the project queue.

**Finance in plain English**
- *Wren*, the copilot, invoices, refunds and chases payments: "invoice CloudPulse for the one-time sprint".
- An LLM picks the PayPal tool and extracts the client and plan, **but nothing that moves money runs without a human click**, and the API rejects it without explicit confirmation.
- For unpaid invoices, an AI collections agent drafts a reminder whose tone matches how overdue it is, and queues it for approval.

## How we built it

- **Backend:** Python with no web framework, SQLite, and a small REST API. The PayPal layer (`paypal.py`) wraps Orders, Subscriptions, Invoicing, Payments and Webhooks, with OAuth token caching and idempotency keys on create, capture and refund.
- **Settlement is atomic.** One transaction captures the payment, moves the deal to Won, records revenue, queues the delivery project and plans its nine tasks. Webhooks are verified with `verify-webhook-signature` and de-duplicated by event ID.
- **AI:** Claude (`claude-haiku-5.5` via OpenRouter) powers the copilot's JSON tool router, proposal summaries, the department deliverables and collections emails. Department agents run in parallel under a semaphore, so we stay inside provider rate limits.
- **Auto-assignment is deterministic and explainable.** For a project needing skills $S_p$, each available member $m$ with skills $S_m$, capacity $c_m$, $a_m$ active and $q_m$ queued items is ranked by

$$\text{rank}(m) = \Big(\,|S_p \cap S_m|,\;\; c_m - a_m - q_m\,\Big)$$

  in lexicographic order: most matching skills first, then most headroom. Members with no headroom, or with no matching skills when skills are required, are skipped, and every decision is stored with its reason.
- **UI:** a single-page app in vanilla JavaScript, with an **AG Grid** PayPal ledger.
- **Tests:** 25 end-to-end tests run against a fake PayPal API, including a sweep that runs every agent, plus 25 crawler tests. A sandbox self-check script verifies a real PayPal account in 30 seconds.
- **The demo video is code.** A storyboard drives a real browser through the live PayPal sandbox, and the narration, captions and speed-ups are rendered automatically, so anyone can reproduce it.

## Challenges we ran into

- **Atomic money flows.** SQLite's `executescript()` silently commits the open transaction. One of our hooks ran inside a PayPal settlement and could have committed a payment halfway through. We caught it and now settlement is all-or-nothing.
- **Agents that are useful *and* safe.** It is tempting to let an agent "just send the invoice." We made money-moving tools return confirmation requests instead. That turned out to make a better product: the agent does the thinking and the person makes the decision.
- **Model reality.** A free reasoning model spent its whole token budget thinking and returned empty answers, and parallel agents tripped rate limits. We added bounded concurrency, a fallback model chain, and honest labelled templates that record *why* the LLM was unavailable, so a template is never passed off as AI output.
- **PayPal's subscription approval** takes several steps (funding source, then consent), and the sandbox is sometimes slow. The demo recorder had to handle both.
- **Doing it responsibly.** The project started as a lead-generation tool with CAPTCHA solvers and LinkedIn scraping. We removed them: the crawler now identifies itself honestly, rate-limits itself, and skips any site that shows a bot challenge.

## Accomplishments that we're proud of

- A real payment in the PayPal sandbox turns into a planned, staffed, documented delivery project, with no manual data entry.
- Nine department agents write client-specific deliverables in seconds, and none of them can ship code or move money on their own.
- It is honest about its limits: agents write plans and documents, and the README says so.

## What we learned

- The most valuable place for AI in a services business is **the handoffs**, not the core craft.
- With agents, *"propose, then confirm"* beats *"act autonomously"* for anything involving money or clients.
- PayPal's Orders, Subscriptions and Invoicing APIs cover the whole commercial lifecycle of a service company, and webhooks make them work like an event bus for the business.

## What's next for Dealwright

- Milestone invoices sent automatically when lifecycle stages are approved, with PayPal **Payouts** for contractors.
- An MCP server, so outside agents can request PayPal actions under the same human-approval rules.
- Profitability per project (PayPal revenue against time spent by people and agents), and real code-review and CI agents for the Engineering department.
