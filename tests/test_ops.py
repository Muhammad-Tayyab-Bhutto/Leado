"""Team & projects administration, auto-assignment, the PayPal -> delivery hook,
and a sweep that runs every workforce agent and copilot intent."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SOFTORASYSTEMS_TOKEN", "ci-test-token-0123456789abcdef")
os.environ["SOFTORA_HOST"] = "127.0.0.1"
os.environ.setdefault("SANDBOX_DISABLE_DOCKER", "1")
for k in ("VULTR_INFERENCE_API_KEY", "VULTR_API_KEY", "OPENROUTER_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
    os.environ[k] = ""  # offline: never reach a real LLM or PayPal from tests

import app  # noqa: E402
import crm  # noqa: E402
import ops  # noqa: E402
import paypal  # noqa: E402
import workforce  # noqa: E402
import copilot  # noqa: E402


class OpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(cls.tmp.name, "leads.db")
        crm.migrate()
        cls.server = crm.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def http(self, method, path, body=None, auth=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if auth: req.add_header("Authorization", "Bearer " + crm.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def member(self, **kw):
        s, m = self.http("POST", "/api/team", {"role": "Engineer", **kw})
        self.assertEqual(s, 200, m); return m

    def test_no_fictional_team_in_a_fresh_database(self):
        names = {m["name"] for m in ops.list_members()}
        self.assertNotIn("Amina Shah", names)

    def test_add_people_and_agents_with_validation(self):
        h = self.member(name="Sara Khan", kind="human", skills="frontend, React ,react", email="Sara@Example.com")
        self.assertEqual((h["kind"], h["availability"], h["skills"], h["email"]), ("human", "free", "frontend,react", "sara@example.com"))
        a = self.member(name="Coder-1", kind="ai_agent", skills="backend,python")
        self.assertEqual((a["kind"], a["capacity_projects"]), ("ai_agent", 5))
        for bad in ({"name": "X", "role": "r", "kind": "robot"}, {"name": "", "role": "r"}, {"name": "Sara Khan", "role": "r"},
                    {"name": "Y", "role": "r", "capacity": 0}, {"name": "Z", "role": "r", "email": "nope"}):
            s, e = self.http("POST", "/api/team", bad)
            self.assertEqual(s, 400, (bad, e))

    def test_availability_follows_real_assignments(self):
        m = self.member(name="Bilal", kind="human", capacity=2)
        s, e = self.http("POST", "/api/projects", {"name": "Unowned"})
        s, e = self.http("PATCH", f"/api/projects/{e['id']}", {"status": "in_progress"})
        self.assertEqual(s, 400)  # cannot start without an owner
        p1 = self.http("POST", "/api/projects", {"name": "Portal v2", "assignee_id": m["id"]})[1]
        self.http("PATCH", f"/api/projects/{p1['id']}", {"status": "in_progress"})
        self.assertEqual(ops.get_member(m["id"])["availability"], "busy")
        self.assertEqual(ops.get_member(m["id"])["working_on"][0]["name"], "Portal v2")
        p2 = self.http("POST", "/api/projects", {"name": "API hardening", "assignee_id": m["id"]})[1]
        self.http("PATCH", f"/api/projects/{p2['id']}", {"status": "review"})
        self.assertEqual(ops.get_member(m["id"])["availability"], "at_capacity")
        self.http("PATCH", f"/api/team/{m['id']}", {"status": "away"})
        self.assertEqual(ops.get_member(m["id"])["availability"], "away")
        done = self.http("PATCH", f"/api/projects/{p1['id']}", {"status": "done"})[1]
        self.assertTrue(done["completed_at"])

    def test_auto_assign_matches_skills_and_capacity(self):
        mobile = self.member(name="Mobile Dev", kind="human", skills="mobile,flutter")
        bot = self.member(name="SEO Bot", kind="ai_agent", skills="seo,content")
        p_seo = self.http("POST", "/api/projects", {"name": "Content refresh", "skills": "seo", "priority": "high"})[1]
        p_app = self.http("POST", "/api/projects", {"name": "Field app", "skills": "mobile"})[1]
        p_none = self.http("POST", "/api/projects", {"name": "Quantum thing", "skills": "quantum"})[1]
        s, res = self.http("POST", "/api/projects/auto-assign", {})
        self.assertEqual(s, 200)
        got = {d["project_id"]: d["member_id"] for d in res["assigned"]}
        self.assertEqual(got[p_seo["id"]], bot["id"])
        self.assertEqual(got[p_app["id"]], mobile["id"])
        self.assertIn(p_none["id"], [x["project_id"] for x in res["skipped"]])

    def test_deactivating_a_member_returns_work_to_the_queue(self):
        m = self.member(name="Leaver", kind="human")
        p = self.http("POST", "/api/projects", {"name": "Handover", "assignee_id": m["id"]})[1]
        self.http("PATCH", f"/api/projects/{p['id']}", {"status": "in_progress"})
        self.http("PATCH", f"/api/team/{m['id']}", {"active": False})
        p = ops.get_project(p["id"])
        self.assertEqual((p["status"], p["assignee_id"]), ("queued", None))
        self.assertNotIn("Leaver", [x["name"] for x in ops.list_members()])
        s, _ = self.http("PATCH", f"/api/projects/{p['id']}", {"assignee_id": m["id"]})
        self.assertEqual(s, 400)  # cannot assign deactivated members

    def test_paid_deal_queues_a_delivery_project_once(self):
        with app.conn() as c:
            c.execute("insert into leads(domain, company, score, service) values('paid.io','PaidCo',80,'devops')")
            lid = c.execute("select id from leads where domain='paid.io'").fetchone()[0]
            c.execute("insert into payments(lead_id,tier_name,kind,paypal_id,amount,currency,interval,status,created_at,updated_at) "
                      "values(?,?,?,?,?,'USD','month','created',?,?)", (lid, "Growth Partner (Recommended)", "subscription", "I-TEST1", 4000, app.now(), app.now()))
        paypal.settle("I-TEST1", "active", source="test")
        paypal.settle("I-TEST1", "active", source="test")  # idempotent
        ps = [p for p in ops.list_projects() if p["lead_id"] == lid]
        self.assertEqual(len(ps), 1)
        self.assertEqual((ps[0]["name"], ps[0]["status"], ps[0]["priority"], ps[0]["skills"]),
                         ("PaidCo: Growth Partner", "queued", "high", "devops,security"))

    def test_team_api_requires_token(self):
        self.assertEqual(self.http("GET", "/api/team", auth=False)[0], 401)
        self.assertEqual(self.http("POST", "/api/projects", {"name": "x"}, auth=False)[0], 401)

    def test_every_workforce_agent_and_copilot_intent_answers(self):
        self.member(name="Sweep Agent", kind="ai_agent", skills="qa")
        failures = []
        for a in workforce.AGENTS:
            try:
                r = workforce.talk("", a["id"])
                if not (r.get("reply") or "").strip(): failures.append((a["id"], "empty reply"))
            except Exception as e:  # pragma: no cover - reported below
                failures.append((a["id"], repr(e)))
        for q in ("Give me a pipeline briefing", "Check sandbox security", "Generate a proposal", "find leads",
                  "who owes us money", "hello"):
            try:
                if not copilot.vio.handle_query(q).get("reply"): failures.append((q, "empty reply"))
            except Exception as e:  # pragma: no cover
                failures.append((q, repr(e)))
        self.assertEqual(failures, [])
        self.assertIn("Projects", workforce.talk("what is in the project queue")["reply"])


if __name__ == "__main__":
    unittest.main()
