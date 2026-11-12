"""Company model: departments, AI staffing, lifecycle planning, agents at work, approvals."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SOFTORASYSTEMS_TOKEN", "ci-test-token-0123456789abcdef")
os.environ.setdefault("SANDBOX_DISABLE_DOCKER", "1")
for k in ("VULTR_INFERENCE_API_KEY", "VULTR_API_KEY", "OPENROUTER_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
    os.environ[k] = ""

import app  # noqa: E402
import crm  # noqa: E402
import ops  # noqa: E402
import org  # noqa: E402
import paypal  # noqa: E402


class OrgTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(self.tmp.name, "leads.db")
        crm.migrate()

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_covers_twenty_departments_and_lifecycle_owners_exist(self):
        self.assertEqual(len(org.DEPARTMENTS), 20)
        self.assertEqual(set(org.AGENT_NAME), set(org.DEPT))
        self.assertTrue(all(s[1] in org.DEPT for s in org.LIFECYCLE))
        ov = org.overview()
        self.assertEqual(len(ov["departments"]), 20)
        self.assertEqual(len(ov["unstaffed"]), 20)

    def test_staffing_fills_only_empty_departments_and_is_idempotent(self):
        human = ops.add_member({"name": "Hira", "role": "QA Lead", "department": "qa"})
        self.assertEqual(human["department"], "qa")
        first = org.staff_with_ai()
        self.assertEqual(len(first["created"]), 19)
        self.assertNotIn("qa", [c["department"] for c in first["created"]])
        self.assertEqual(org.staff_with_ai()["created"], [])
        self.assertEqual(org.overview()["unstaffed"], [])
        with self.assertRaises(ValueError):
            ops.add_member({"name": "X", "role": "r", "department": "astrology"})

    def test_new_project_is_planned_across_departments(self):
        org.staff_with_ai()
        p = ops.create_project({"name": "Portal"})
        tasks = org.list_tasks(p["id"])
        self.assertEqual([t["stage"] for t in tasks], [s[0] for s in org.LIFECYCLE])
        self.assertTrue(all(t["assignee_kind"] == "ai_agent" for t in tasks))
        self.assertEqual(ops.get_project(p["id"])["assignee_name"], "PM Agent")  # the PM owns the project
        org.plan_project(p["id"])  # idempotent
        self.assertEqual(len(org.list_tasks(p["id"])), len(org.LIFECYCLE))

    def test_agents_work_then_humans_approve(self):
        org.staff_with_ai()
        p = ops.create_project({"name": "Mobile app"})
        tasks = org.list_tasks(p["id"])
        with self.assertRaises(ValueError):  # cannot approve work that was never produced
            org.update_task(tasks[0]["id"], {"status": "done"})
        res = org.run_all(p["id"])
        self.assertEqual((res["ran"], res["errors"]), (len(org.LIFECYCLE), {}))
        self.assertTrue(all(t["status"] == "review" and t["output"] for t in res["tasks"]))
        self.assertEqual(ops.get_project(p["id"])["status"], "in_progress")
        reviewer = next(m for m in ops.list_members() if m["name"] == "QA Agent")
        self.assertEqual(reviewer["availability"], "busy")  # output waiting for review counts as active work
        for t in res["tasks"]:
            org.update_task(t["id"], {"status": "done"})
        self.assertEqual(ops.get_project(p["id"])["status"], "review")
        with self.assertRaises(ValueError):
            org.run_task(res["tasks"][0]["id"])  # already done

    def test_people_own_their_tasks(self):
        pm = ops.add_member({"name": "Usman", "role": "PM", "department": "project_management"})
        ba = ops.add_member({"name": "Sana", "role": "Business Analyst", "department": "presales"})
        p = ops.create_project({"name": "ERP rollout"})
        t = next(x for x in org.list_tasks(p["id"]) if x["stage"] == "requirements")
        self.assertEqual(t["assignee_id"], ba["id"])
        with self.assertRaises(ValueError):
            org.run_task(t["id"])  # people do their own work
        self.assertEqual(org.update_task(t["id"], {"status": "done"})["status"], "done")
        self.assertEqual(ops.get_project(p["id"])["assignee_id"], pm["id"])

    def test_paid_deal_is_planned_in_the_same_transaction(self):
        org.staff_with_ai()
        with app.conn() as c:
            c.execute("insert into leads(domain, company, score, service) values('pay.io','PayCo',80,'saas')")
            lid = c.execute("select id from leads where domain='pay.io'").fetchone()[0]
            c.execute("insert into payments(lead_id,tier_name,kind,paypal_id,amount,currency,interval,status,created_at,updated_at) "
                      "values(?,?,?,?,?,'USD','once','created',?,?)", (lid, "Sprint", "order", "ORD-ORG-1", 2000, app.now(), app.now()))
        paypal.settle("ORD-ORG-1", "completed", capture_id="CAP1", source="test")
        p = next(p for p in ops.list_projects() if p["lead_id"] == lid)
        self.assertEqual(len(org.list_tasks(p["id"])), len(org.LIFECYCLE))
        with app.conn() as c:
            self.assertEqual(c.execute("select status from leads where id=?", (lid,)).fetchone()[0], "WON")
            self.assertEqual(c.execute("select status from payments where paypal_id='ORD-ORG-1'").fetchone()[0], "completed")
        self.assertEqual(org.overview()["departments"][15]["id"], "finance")

    def test_deliverable_envelope_is_unwrapped(self):
        self.assertEqual(org._unwrap('{"deliverable": "**Plan**\n- a"}'), "**Plan**\n- a")  # raw newline inside string
        self.assertEqual(org._unwrap('```json\n{"deliverable": "**A**"}\n```'), "**A**")
        self.assertEqual(org._unwrap('{"deliverable": "**Cut**\\n- off'), "**Cut**\n- off")  # truncated output
        self.assertEqual(org._unwrap("plain markdown"), "plain markdown")


if __name__ == "__main__":
    unittest.main()
