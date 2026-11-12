# Submission kit: Dealwright

## Files
- Video: `demo/dealwright-demo.mp4` (1:50, 1920×1080, H.264/AAC, narrated, captions burned in)
- Captions: `demo/dealwright-demo.srt` (upload to YouTube as English subtitles)

## YouTube upload
**Visibility:** Public (required by the rules)

**Title**
Dealwright: AI quote-to-cash on PayPal (Build What's Next with PayPal and AI)

**Description**
Dealwright is an AI sales agent that takes a B2B deal from research to money in PayPal, with a human approving every step that moves money.

In this demo (recorded live against the PayPal sandbox):
0:00 The problem: deals stall between "proposal sent" and "paid"
0:10 AI-researched lead with evidence from the prospect's own site
0:17 AI-written proposal with three pricing tiers
0:25 One-click PayPal checkout link (Orders v2 for one-time, Subscriptions v1 for monthly)
0:33 The client's view, then PayPal sandbox login and Agree & Subscribe (sped up)
0:56 Deal moves to WON automatically, with recorded MRR
1:05 PayPal payments ledger built with AG Grid
1:11 Wren, the AI agent: "Invoice CloudPulse for the one-time sprint" sends a PayPal invoice after human confirmation
1:24 AI collections: tone-aware payment reminder queued for approval
1:36 PayPal APIs and tools used

PayPal: Orders v2, Subscriptions v1, Invoicing v2, Payments v2 (refunds), Webhooks (signature-verified)
AI: Claude (via OpenRouter) for the tool router, proposal summaries and collections emails
Also: AG Grid Community, Render blueprint
Code: https://github.com/Muhammad-Tayyab-Bhutto/Dealwright

**Tags**
PayPal, PayPal API, AI agent, agentic commerce, hackathon, invoicing, subscriptions, AG Grid, Claude, sales automation

## Submission form

**Project name:** Dealwright

**Tagline:** An AI sales agent that turns proposals into PayPal payments, with a human approving every money move.

**Text description**
Agencies and B2B service teams lose deals in the gap between "proposal sent" and "got paid". Dealwright closes that gap in one flow. It researches a lead, quotes evidence from the prospect's own website, and writes a scoped proposal with three pricing tiers. One click turns the proposal into a client pay page: one-time tiers check out through PayPal Orders v2, and monthly tiers create a PayPal product, plan and subscription on the fly. When PayPal confirms (on the return URL or through a signature-verified webhook), the deal moves to Won and MRR is recorded, with no manual data entry.

Wren, the built-in agent, handles billing in plain English: "invoice CloudPulse for the one-time sprint", "who owes us money?", "refund DataStream". An LLM tool router picks the PayPal tool and extracts the company and tier, but Wren never moves money on its own. Invoices, refunds and cancellations come back as confirmation buttons, and the API rejects them without explicit confirmation. For unpaid items, an AI collections agent writes a reminder whose tone depends on how long the payment has been outstanding, and queues it for human approval. Every payment appears in an AG Grid ledger with collected, MRR, outstanding and refunded totals.

Responsible by design: unguessable, throttled, single-use pay links; webhook verification with dedupe; no CAPTCHA solving, proxy rotation or LinkedIn scraping; human approval for all outreach.

**Tools used and how**
- PayPal Orders v2: one-time checkout (create, then capture on return), with idempotency keys
- PayPal Subscriptions v1: catalog product, billing plan and subscription created from the AI-priced tier
- PayPal Invoicing v2: Wren's send_invoice tool
- PayPal Payments v2: refunds; subscription cancel
- PayPal Webhooks: PAYMENT.CAPTURE.*, BILLING.SUBSCRIPTION.*, INVOICING.INVOICE.PAID, all verified with verify-webhook-signature
- Claude (anthropic/claude-haiku-5.5 via OpenRouter): Wren's JSON tool router, proposal executive summaries, collections emails; deterministic fallbacks when no key is set
- AG Grid Community v33: PayPal payments ledger
- Render: render.yaml Blueprint for a hosted demo

**How to run:** see the README "Quick start for judges". `python scripts/paypal_selfcheck.py` verifies PayPal sandbox keys in about 30 seconds.

**Repository:** https://github.com/Muhammad-Tayyab-Bhutto/Dealwright (MIT license)
**Video:** <paste the YouTube link here>
