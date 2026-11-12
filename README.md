# Dealwright: AI quote-to-cash on PayPal

> An AI sales agent that finds a client, diagnoses their problem, writes the proposal, and gets it **paid through PayPal**, with a human approving every step that moves money.

[![CI](https://github.com/Muhammad-Tayyab-Bhutto/Dealwright/actions/workflows/ci.yml/badge.svg)](https://github.com/Muhammad-Tayyab-Bhutto/Dealwright/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PayPal Sandbox](https://img.shields.io/badge/PayPal-Sandbox-003087?logo=paypal&logoColor=white)](https://developer.paypal.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

![Dealwright: your whole software company, run by people and AI agents, on PayPal](docs/assets/cover.png)

Agencies and B2B service teams lose deals in the gap between *"proposal sent"*
and *"got paid"*. Proposals sit in PDFs, invoices go out days later, and nobody
chases the unpaid ones. Dealwright closes that gap:

```mermaid
flowchart LR
    A[Find & research lead<br/>AI] --> B[Diagnose problem<br/>AI + sandboxed audit]
    B --> C[Write SOW + 3 pricing tiers<br/>AI]
    C --> D[Client pay page<br/>PayPal Orders / Subscriptions]
    D -->|capture or webhook| E[Deal auto-moves to WON<br/>MRR updated]
    C --> F[Wren: invoice Acme<br/>PayPal Invoicing]
    F --> G[Unpaid? AI drafts reminder<br/>human approves]
    E --> H[Wren: refund / cancel<br/>PayPal Payments]
```

## What it does

| Step | What happens | AI | PayPal |
|---|---|---|---|
| 1. Discover | Multi-source lead discovery and technographic research | LLM research and scoring | |
| 2. Propose | Executive SOW with a 3-phase scope and three pricing tiers | LLM writes the summary from audit evidence | |
| 3. **Get paid** | One click creates a client pay link. One-time tiers check out with **Orders v2**; monthly tiers create a **product, plan and subscription** on the fly | | Orders v2, Subscriptions v1 |
| 4. **Close the deal** | On capture (return URL) or a **signature-verified webhook**, the lead moves to WON, MRR or project value is recorded, and the timeline is updated | | Webhooks, verify-webhook-signature |
| 5. **Agentic billing** | Ask **Wren**: *"invoice FinFlow for the monthly plan"*, *"who owes us money?"*, *"refund DataStream"* | An LLM tool router picks the PayPal tool and extracts the company and tier | Invoicing v2, Refunds, Subscription cancel |
| 6. **AI collections** | For outstanding payments, the AI writes a reminder whose tone matches how long the payment has been open, with the pay link, and queues it in **Approvals** | LLM writes the reminder | Pay link / invoice link |

**Safety model: the AI proposes, a person approves, PayPal executes.** Wren
never moves money on its own. Invoices, refunds and cancellations come back as
confirmation buttons, and the API rejects them without `confirm: true`.
Reminder emails go through the existing human-approval and suppression gates.

## Company: 20 departments, people and AI agents, one lifecycle

Dealwright models a full software house: **20 departments** in four pillars (Revenue, Delivery, Operations, Product & R&D), from Sales, Marketing and Pre-Sales through Engineering, Design, QA, DevOps and Cybersecurity, to Finance, HR, Legal and Customer Success. Each department is staffed by people, AI agents, or both, and shows live KPIs: sales conversion, PayPal collections and MRR, active and overdue projects, and task throughput.

**Client-to-delivery lifecycle.** When PayPal confirms a payment, the Project Manager agent plans nine stages and routes each one to the department that owns it:

| # | Stage | Department |
|---|---|---|
| 1 | Requirement analysis & estimate | Business Analysis & Pre-Sales |
| 2 | Sprint plan & milestones | Project & Program Management |
| 3 | UX flows & wireframe spec | UI/UX & Product Design |
| 4 | Architecture & API plan | Software Engineering |
| 5 | Test plan & acceptance criteria | Quality Assurance |
| 6 | Security review checklist | Cybersecurity |
| 7 | Deployment & monitoring runbook | DevOps & Cloud |
| 8 | Client handover & success plan | Customer Support & Success |
| 9 | Milestone billing plan | Finance & Accounting |

Tasks owned by AI agents run in parallel, with bounded concurrency. Each agent writes its department's deliverable for this specific client using the LLM, and the task waits for **human approval**. Tasks owned by people stay with them. When the LLM is unavailable, the agent produces a clearly labelled template and records the reason. **Agents write plans and documents; they do not ship code or move money.**

`POST /api/org/staff-ai` fills empty departments with AI agents. Real staff are added from Team & Projects with a department.

## Team & Projects: run the delivery side of a software company

Sales and money are only half of a services company. The **Team & Projects** view is the admin console for delivery:

- **One roster for people and AI agents.** Add employees and AI agents with a role, skills and project capacity. Deactivating someone keeps their history and returns their open work to the queue.
- **Who is working on what, and who is free.** Availability is computed from real assignments (free, busy, at capacity), or set to away by hand. Each member shows the projects in progress, any queued work, and load against capacity.
- **Project queue.** Queued → In progress → Review → Done, with priority, due dates, overdue flags, and assignment from a dropdown.
- **Paid deals become projects.** When a PayPal payment settles, a high-priority delivery project is queued automatically, with skills inferred from the service sold.
- **Explainable auto-assign.** Each unassigned project goes to the available member, human or AI agent, with the best skill match and the most headroom. Every decision comes with its reason, and projects nobody is qualified for are left unassigned rather than forced on someone.
- **Ask in plain language.** Voice agents such as "who is free", "who is busy", "workload" and "what is in the project queue" answer from the same data.

## Quick start for judges (about 10 minutes)

### 1. PayPal sandbox credentials
1. Sign in at <https://developer.paypal.com>, then go to **Apps & Credentials** → **Sandbox** → **Create App**.
2. Copy the **Client ID** and **Secret**.
3. Under **Testing Tools → Sandbox Accounts**, note the **Personal** account email and password. You'll use it to pay as the client.

### 2. Check your PayPal keys (about 30 seconds)
```bash
python scripts/paypal_selfcheck.py   # PASS/FAIL for OAuth, Orders, Subscriptions, Invoicing, Webhooks (sandbox only)
```

### 3. Run
```bash
git clone https://github.com/Muhammad-Tayyab-Bhutto/Dealwright.git && cd Dealwright
cp .env.example .env
#   SOFTORASYSTEMS_TOKEN=<any 16+ char secret>
#   PAYPAL_CLIENT_ID=...  PAYPAL_CLIENT_SECRET=...
#   OPENROUTER_API_KEY=... OPENROUTER_MODEL=<a fast chat model>   (optional; AI falls back to templates)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python seed_demo_pipeline.py          # 5 demo leads with proposals
python app.py serve 8000
```
Or run `docker compose up --build`. Open <http://localhost:8000> and enter your token.

### 4. Try the flow
1. **Proposals** → open any proposal → **💳 PayPal checkout link** → open the link in a private window.
2. Pick a tier → **Pay with PayPal** → sign in with the sandbox **Personal** account → approve.
3. You land on the confirmation page. In the CRM, the lead is now **WON** and appears in **PayPal Payments** with its capture ID.
4. **Wren AI Copilot** → *"Who still owes us money on PayPal?"* → **AI reminder**, then *"invoice CloudPulse for the monthly growth plan"* → confirm.
5. **PayPal Payments** → **Refund** (or **Cancel** for subscriptions).

Webhooks are optional locally, because the buyer return URL captures the
payment. When the app is deployed, add a webhook in your PayPal app pointing to
`https://<host>/api/paypal/webhook` and set `PAYPAL_WEBHOOK_ID`.

### Hosted demo
Deploy with **Render**: `render.yaml` is a ready Blueprint. Set
`PUBLIC_BASE_URL` to the Render URL so PayPal return links point back to it.

## Tools used

| Tool | How it's used |
|---|---|
| **PayPal Orders v2** | One-time tier checkout: create the order with return/cancel URLs, then capture on return. Idempotency keys on create, capture and refund. |
| **PayPal Subscriptions v1** | Monthly tiers: a catalog product, billing plan and subscription are created from the AI-priced tier. |
| **PayPal Invoicing v2** | Wren's `send_invoice` tool: draft and send an invoice to the lead's contact. |
| **PayPal Payments v2** | Refunds of captured payments; cancelling subscriptions. |
| **PayPal Webhooks** | `PAYMENT.CAPTURE.COMPLETED/REFUNDED`, `BILLING.SUBSCRIPTION.ACTIVATED/CANCELLED`, `PAYMENT.SALE.COMPLETED`, `INVOICING.INVOICE.PAID`. Each event is checked with `verify-webhook-signature` and de-duplicated by event ID. |
| **AG Grid Community** (v33) | The PayPal Payments ledger: sorting, floating filters, number filters on amount and age, status pills, inline Sync / AI reminder / Refund actions, and amber highlighting for payments open more than 7 days. Falls back to a plain table if the CDN is unreachable. |
| **Render** | `render.yaml` Blueprint for a hosted demo (seeds demo data on boot). |
| **LLM via OpenRouter** (any model, e.g. Claude, GPT, Llama), or Vultr Serverless Inference | Lead research and scoring, proposal executive summaries, Wren's tool router (JSON tool selection plus company and tier extraction), and the collections agent that writes reminders. If no key is set, everything falls back to deterministic logic. |

## Architecture

| File | Role |
|---|---|
| `paypal.py` | PayPal REST client (OAuth token cache), checkout, subscriptions, invoicing, refunds, sync, webhooks, AI reminders |
| `paypal_web.py` | Public client pay page, scoped to an unguessable per-proposal token |
| `copilot.py` | Wren: LLM tool router plus PayPal tools, with confirmation gating |
| `crm.py` | HTTP server and REST API. Public routes are only `/`, `/pay/<token>…` and `/api/paypal/webhook` |
| `proposal_engine.py` | SOW plus pricing tiers (now with numeric `amount` / `interval` for PayPal) |
| `agent.py` | LLM client (OpenRouter / Vultr) with a circuit breaker and daily cap |
| `org.py` | 20-department company model, lifecycle planning, department AI agents, KPIs |
| `ops.py` | Team roster (people and AI agents), project queue, availability, auto-assign, PayPal → project hook |
| `ui.html` | Single-page CRM: pipeline, proposals, **PayPal Payments**, **Team & Projects**, Wren |
| `app.py`, `lead_finder/`, `sandbox.py`, `growth.py`, `workforce.py` | Discovery, scoring, sandboxed audits, outreach |

### PayPal API surface
| Route | Auth | Purpose |
|---|---|---|
| `GET /pay/{token}` | public (token) | Client proposal and pricing with PayPal buttons |
| `POST /pay/{token}/checkout` | public (token) | Create the order or subscription, then 303 redirect to PayPal |
| `GET /pay/{token}/return` | public (token) | Capture the order / confirm the subscription, then mark WON |
| `POST /api/paypal/webhook` | PayPal signature | Event sync |
| `GET /api/paypal/status`, `GET /api/payments` | bearer | Status, ledger, collected / MRR / outstanding |
| `POST /api/proposals/{id}/paylink` | bearer | Create the client pay link |
| `POST /api/leads/{id}/invoice` | bearer + `confirm` | Send a PayPal invoice |
| `POST /api/payments/{id}/refund` | bearer + `confirm` | Refund or cancel |
| `POST /api/payments/{id}/sync` | bearer | Pull the latest state from PayPal |
| `POST /api/payments/{id}/remind` | bearer | AI collections draft (queued for approval) |
| `GET/POST /api/team`, `PATCH /api/team/{id}` | bearer | Roster of people and AI agents; availability, away, deactivate |
| `GET/POST /api/projects`, `GET/PATCH /api/projects/{id}` | bearer | Delivery projects: status, assignee, priority, due |
| `POST /api/projects/auto-assign` | bearer | Skill- and capacity-based assignment, with a reason for each decision |
| `GET /api/org`, `POST /api/org/staff-ai` | bearer | Departments, staffing and KPIs; staff empty departments with AI agents |
| `GET /api/projects/{id}/tasks`, `POST /api/projects/{id}/plan` | bearer | The 9-stage lifecycle of a project |
| `POST /api/projects/{id}/run-agents`, `POST /api/tasks/{id}/run` | bearer | AI agents write their deliverables (then wait for approval) |
| `PATCH /api/tasks/{id}` | bearer | Approve (`done`), block, or reassign a task |

## Responsible by design

- **Money:** the AI proposes, a person confirms, and PayPal executes. Money-moving APIs require `confirm: true`. Pay links are unguessable, scoped to one proposal, throttled, and can't be paid twice. Internal lead scores are never shown on client pages.
- **Webhooks:** every event is verified with PayPal's `verify-webhook-signature` and de-duplicated by event ID.
- **Crawling:** the crawler uses an honest user agent and robots.txt checks, rate-limits with backoff, and uses **no** proxy or user-agent rotation. **CAPTCHAs are never solved**: a bot challenge means the site is skipped. No LinkedIn scraping.
- **Outreach:** every email (cold or collections) needs human approval and goes through suppression lists and CAN-SPAM address checks.
- **Untrusted audits** run in a throwaway container with no host environment variables. The live containment proof checks that no PayPal, LLM or app secrets are visible.

## Testing

```bash
SANDBOX_DISABLE_DOCKER=1 python -m unittest discover -s tests -v
```

`lead_finder/tests.py` checks the polite-crawling guarantees.
`tests/test_paypal.py` runs the full quote-to-cash flow over real HTTP against
an in-memory fake PayPal API. It covers pay link → checkout redirect → capture →
WON, subscription → MRR, the invoice confirmation gate, the AI reminder draft,
refunds, idempotent returns, rejection of unsigned webhooks, and Wren's
routing of money actions to confirmations. No keys or network needed.

## Built during the PayPal hackathon

The whole project was created during the submission period (first commit
2026-11-09). It began as "Leado", an AI lead-generation CRM (discovery,
research, proposals, sandboxed audits) that we first prototyped for the
LabLab × Vultr Agent Rush hackathon. That prototype had **no payment
functionality**. For this hackathon we:

- **added the whole money side:** `paypal.py`, `paypal_web.py`, the PayPal
  Payments view, Wren's PayPal tools and LLM tool router, the AI collections
  agent, the webhook pipeline, and machine-readable pricing tiers;
- **made crawling compliant:** removed the CAPTCHA-solving integrations, the
  proxy and user-agent rotation, and LinkedIn scraping. The crawler now uses an
  honest user agent, rate-limits itself, and skips any site that shows a bot
  challenge (`lead_finder/politeness.py`, `lead_finder/captcha.py`);
- **renamed the product** to Dealwright to avoid confusion with unrelated
  products called Dealwright;
- added `render.yaml` and `tests/test_paypal.py`.

Every change is in the git history after commit `884006f`.

## Recording the demo video

The demo video is generated from code: `scripts/demo/storyboard.json` drives a real
browser through the live PayPal sandbox, then adds offline narration (Piper), captions
and speed-ups with ffmpeg.

```bash
pip install -r scripts/demo/requirements.txt     # plus google-chrome and ffmpeg on PATH
# .env: PayPal sandbox keys, DEMO_PAYPAL_BUYER_EMAIL / _PASSWORD (a sandbox *Personal* account),
#       DEMO_VOICE=/path/to/en_US-ryan-high.onnx (https://huggingface.co/rhasspy/piper-voices)
scripts/demo/record.sh                          # -> demo/dealwright-demo.mp4 + .srt
```

## Self-hosting on a VM

`deploy_vultr.sh` and [`docs/DEPLOY_VULTR.md`](docs/DEPLOY_VULTR.md) provide a one-command Docker +
Caddy (automatic HTTPS) deployment on any Ubuntu/Debian VM.

## License

[MIT](LICENSE) © 2026 Muhammad Tayyab Bhutto.
