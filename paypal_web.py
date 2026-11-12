"""Public, token-scoped client pages: view the AI-generated proposal and pay it with PayPal."""
import html
import re
import paypal

E = html.escape

CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7fb;color:#111827}
.w{max-width:860px;margin:0 auto;padding:32px 16px 64px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;margin-bottom:16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px;color:#374151}.m{color:#6b7280;font-size:13px}
ul{padding-left:20px;margin:6px 0}.tiers{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.tier{border:1px solid #e5e7eb;border-radius:12px;padding:18px;display:flex;flex-direction:column}.tier.rec{border:2px solid #0070e0}.tier .tag{align-self:flex-start;margin-bottom:8px}
.price{font-size:22px;font-weight:700;margin:6px 0}.tier form{margin-top:auto;padding-top:12px}
.pp{width:100%;border:0;border-radius:999px;padding:12px;font-weight:700;font-size:15px;cursor:pointer;background:#ffc439;color:#003087}
.pp:hover{filter:brightness(.96)}.ok{border-left:4px solid #16a34a}.bad{border-left:4px solid #dc2626}
.foot{text-align:center;color:#9ca3af;font-size:12px;margin-top:24px}.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:2px 10px;font-size:12px}
"""

def page(title, body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><div class='w'>{body}"
            f"<div class='foot'>Payments processed securely by PayPal{' (sandbox)' if paypal.env() == 'sandbox' else ''} · {E(paypal.brand())}</div>"
            f"</div></body></html>")

def proposal_page(tok, notice=""):
    p, l = paypal.proposal_for_token(tok)
    tiers = paypal._proposal_tiers(p)
    import json
    def js(k):
        v = p.get(k)
        if isinstance(v, str):
            try: return json.loads(v)
            except Exception: return []
        return v or []
    paid = p.get("status") == "paid"
    tier_html = []
    for i, t in enumerate(tiers):
        try: amt, iv = paypal.tier_amount(t)
        except ValueError: continue
        label = f"${amt:,.0f}" + (" / month" if iv == "month" else " one-time")
        btn = ("<p class='m'>✔ Already paid</p>" if paid else
               f"<form method='post' action='/pay/{E(tok)}/checkout'><input type='hidden' name='tier' value='{i}'>"
               f"<button class='pp' type='submit'>{'Subscribe' if iv == 'month' else 'Pay'} with PayPal</button></form>")
        feats = "".join(f"<li>{E(str(f))}</li>" for f in t.get("features", []))
        rec = "recommend" in t.get("name", "").lower()
        nm = re.sub(r"\s*\(recommended\)", "", t.get("name", ""), flags=re.I)
        tier_html.append(f"<div class='tier{' rec' if rec else ''}'>{'<span class=tag>Recommended</span>' if rec else ''}<b>{E(nm)}</b>"
                         f"<div class='price'>{label}</div><span class='m'>{E(t.get('highlight', ''))}</span><ul>{feats}</ul>{btn}</div>")
    phases = "".join(f"<li><b>{E(s.get('phase', ''))}</b> <span class='m'>({E(s.get('duration', ''))})</span></li>" for s in js("scope_of_work"))
    internal = ("lead score", "target contact", "current service alignment", "verified source")  # sales-side data, never shown to the client
    findings = "".join(f"<li>{E(str(f))}</li>" for f in js("audit_findings") if not str(f).lower().startswith(internal))
    body = (notice +
            f"<div class='card'><span class='tag'>Proposal for {E(l.get('company') or '')}</span><h1>{E(p.get('title', ''))}</h1>"
            f"<p>{E(p.get('executive_summary') or '')}</p></div>"
            + (f"<div class='card'><h2>What we found</h2><ul>{findings}</ul></div>" if findings else "")
            + (f"<div class='card'><h2>Scope of work</h2><ul>{phases}</ul></div>" if phases else "")
            + f"<div class='card'><h2>Choose your plan</h2><div class='tiers'>{''.join(tier_html)}</div></div>")
    return page(p.get("title") or "Proposal", body)

def notice(ok, msg):
    return f"<div class='card {'ok' if ok else 'bad'}'><b>{E(msg)}</b></div>"

def thanks(tok, pay):
    what = "Your subscription is active" if pay["kind"] == "subscription" else "Payment received"
    amt = f"${pay['amount']:,.2f}{' / month' if pay['interval'] == 'month' else ''}"
    return proposal_page(tok, notice(True, f"✅ {what}: {pay['tier_name']} — {amt}. Thank you! Our team has been notified and will reach out to kick off."))
