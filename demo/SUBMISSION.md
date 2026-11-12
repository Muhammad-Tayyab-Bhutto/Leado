# Submission kit: Dealwright

## Files
- Video: `demo/dealwright-demo.mp4` (1:50, 1920×1080, H.264/AAC, narrated, captions burned in)
- Captions: `demo/dealwright-demo.srt` (upload to YouTube as English subtitles)

## Before you submit
- [ ] GitHub repo is **public** (Settings → General → Danger Zone → Change visibility). It is currently private.
- [ ] Optional: rename the repo to `Dealwright` (Settings → General → Repository name). Old `…/Leado` links keep redirecting, so the link in the video stays valid.
- [ ] The MIT license shows in the repo's About section.
- [ ] The YouTube video is **Public** and its link is pasted below and in the form.

## YouTube upload
**Visibility:** Public (required by the rules)

**Title**
Dealwright: a software company run by people and AI agents, on PayPal

**Description**
Dealwright runs a software house end to end: AI sales agents win the client, PayPal collects the money, and department agents plan and document the delivery, with a human approving every deliverable and every money move.

Recorded live against the PayPal sandbox:
0:00 The problem: the gap between a signed deal and a paid, delivered project
0:10 The company: 20 departments staffed by people and AI agents, with live KPIs
0:18 Sales agent: a lead with evidence from the prospect's own website
0:24 Proposal agent: a scoped proposal with three pricing tiers
0:32 One-click PayPal checkout link (Orders v2 for one-time, Subscriptions v1 for retainers)
0:38 The client's view, then PayPal sandbox login and Agree & Subscribe (sped up)
0:53 Subscription active: the deal is Won with no manual data entry
0:57 The PM agent plans a 9-stage delivery lifecycle across departments
1:04 Department AI agents write their deliverables in parallel (sped up)
1:17 Client-specific deliverables await human approval; Security stays with a person
1:25 Finance agent: a PayPal invoice in plain English, sent after human confirmation
1:40 PayPal APIs and tools used

PayPal: Orders v2, Subscriptions v1, Invoicing v2, Payments v2 (refunds), Webhooks (signature-verified)
AI: Claude (claude-haiku-5.5 via OpenRouter) for the copilot's tool router, proposals, department deliverables and collections
Also: AG Grid Community, Render blueprint
Code: https://github.com/Muhammad-Tayyab-Bhutto/Leado

**Tags**
PayPal, PayPal API, AI agents, agentic commerce, multi-agent, software house, hackathon, subscriptions, invoicing, AG Grid, Claude

## Submission form

**Project name:** Dealwright

**Tagline:** Your whole software company, run by people and AI agents, from first lead to paid delivery on PayPal.

**Text description**
A software house lives or dies on the gap between a signed deal and a paid, delivered project. Dealwright runs that whole journey in one system.

Revenue: sales agents research prospects and quote evidence from the prospect's own website. A proposal agent writes a scoped proposal with three pricing tiers. One click turns it into a client pay page: one-time tiers check out through PayPal Orders v2, and monthly tiers create a PayPal product, plan and subscription on the fly. When PayPal confirms the payment (on the return URL or through a signature-verified webhook), the deal moves to Won and revenue is recorded, with no manual data entry.

Delivery: the paid deal becomes a project. A Project Manager agent plans a 9-stage lifecycle (requirements and estimate, sprint plan, UX, architecture, test plan, security review, deployment runbook, handover, milestone billing) and routes each stage to the department that owns it. Department AI agents write their deliverable for that specific client, and each one waits for human approval. Tasks owned by people stay with them.

The company: 20 departments in four pillars (Revenue, Delivery, Operations, Product & R&D), staffed by people, AI agents, or both, with live KPIs. Admins add employees and AI agents, see who is free or busy and what they are working on, and auto-assign the project queue by skills and capacity, with a reason for every decision.

Finance in plain English: Wren, the copilot, invoices, refunds and chases payments ("invoice CloudPulse for the one-time sprint"). An LLM picks the PayPal tool, but nothing that moves money runs without a human click, and the API rejects it without explicit confirmation.

Responsible by design: agents write plans and documents; they do not ship code or move money. Pay links are unguessable, throttled and single-use. Webhooks are verified and de-duplicated. The crawler does no CAPTCHA solving, proxy rotation or LinkedIn scraping.

**Tools used and how**
- PayPal Orders v2: one-time checkout (create, then capture on return), with idempotency keys
- PayPal Subscriptions v1: catalog product, billing plan and subscription created from the AI-priced tier
- PayPal Invoicing v2: the copilot's send_invoice tool
- PayPal Payments v2: refunds; subscription cancel
- PayPal Webhooks: PAYMENT.CAPTURE.*, BILLING.SUBSCRIPTION.*, INVOICING.INVOICE.PAID, verified with verify-webhook-signature
- Claude (anthropic/claude-haiku-5.5 via OpenRouter): copilot tool router, proposal summaries, the 9 department agents' deliverables, collections emails; labelled templates when the LLM is unavailable
- AG Grid Community v33: PayPal payments ledger
- Render: render.yaml Blueprint for a hosted demo

**How to run:** see the README "Quick start for judges". `python scripts/paypal_selfcheck.py` verifies PayPal sandbox keys in about 30 seconds.

**Repository:** https://github.com/Muhammad-Tayyab-Bhutto/Leado (MIT license)
**Video:** <paste the YouTube link here>

## Project Story
Paste the contents of `demo/STORY.md` into "About the project" (Markdown with LaTeX).

## Built with (25 tags)
python, javascript, html5, css3, sqlite, paypal, paypal-rest-api, paypal-orders, paypal-subscriptions, paypal-invoicing, paypal-webhooks, claude, anthropic, openrouter, llm, ai-agents, multi-agent, ag-grid, docker, caddy, render, github-actions, chrome-devtools-protocol, ffmpeg, piper-tts

## "Try it out" links
- Source code: https://github.com/Muhammad-Tayyab-Bhutto/Leado (make it public first)
- Demo video: <YouTube link>
- Live demo: https://leado.softorasystems.com. **Only add this after the new version is deployed.** It currently runs the old Vultr build. Merging `paypal-hackathon` into `main` triggers the deploy; set PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and PUBLIC_BASE_URL in the server's `.env` first.

## Image gallery (demo/gallery/, 1800×1200 = 3:2, PNG, each < 5 MB)
Upload in order: 01-cover first (it becomes the thumbnail), then 02 to 11.
