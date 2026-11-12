"""Start Dealwright on a fresh, seeded demo database for recording the demo video.

    python scripts/demo/serve_demo.py            # http://localhost:8791, token: demo-recording-token-000000

Never touches leads.db. Uses PayPal sandbox keys and the LLM from .env.
"""
import os, shutil, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

PORT = int(os.environ.get("DEMO_PORT", "8791"))
os.environ["SOFTORASYSTEMS_TOKEN"] = os.environ.get("DEMO_TOKEN", "demo-recording-token-000000")
os.environ.setdefault("PUBLIC_BASE_URL", f"http://localhost:{PORT}")
os.environ.setdefault("SANDBOX_DISABLE_DOCKER", "1")
os.environ.setdefault("PAYPAL_BRAND_NAME", "Dealwright")

data = os.path.join(ROOT, "scripts", "demo", ".demo")
shutil.rmtree(data, ignore_errors=True); os.makedirs(data)
import app
app.DB = os.path.join(data, "demo.db")
import crm, seed_demo_pipeline
crm.migrate()
seed_demo_pipeline.seed()
buyer = os.environ.get("DEMO_PAYPAL_BUYER_EMAIL")
with app.conn() as c:
    if buyer:  # invoices in the demo go to the sandbox buyer so they can actually be paid
        c.execute("update leads set contact_email=? where company like 'CloudPulse%'", (buyer,))
    c.execute("update leads set approved=1")
crm.serve(host="127.0.0.1", port=PORT)
