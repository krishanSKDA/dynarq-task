import http.client
import json
import os
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from app import make_server

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


class AppTest(unittest.TestCase):
    def start(self, data_dir=DATA_DIR, today="2026-10-12"):
        server = make_server(0, data_dir, today)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        return server

    def request(self, method, path, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = urlencode(form) if form else None
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if form else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        conn.close()
        return response.status, text

    def temp_data(self, invoices, sent):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, content in [("invoices.json", invoices), ("sent.json", sent)]:
            with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
        return tmp.name

    def test_shows_plan_from_rules_engine(self):
        self.start()
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        for text in ["Alder Fabrication Ltd", "INV-1001", "EUR 1,250.00", "Firm reminder",
                     "Final notice", "Friendly reminder", "USD 845.25", "No approval needed"]:
            self.assertIn(text, html)
        self.assertNotIn("Copperleaf Catering", html)

    def test_skipped_in_plain_words(self):
        self.start()
        _, html = self.request("GET", "/")
        self.assertIn("The due date is not a real date", html)
        self.assertIn("The invoice has no due date.", html)
        self.assertIn("appears more than once", html)
        for code in ["invalid_due_date", "missing_due_date", "duplicate_id"]:
            self.assertNotIn(code, html)

    def test_approve_reject_and_undo(self):
        server = self.start()
        status, _ = self.request("POST", "/decide", {"invoice_id": "INV-1001", "step": "firm", "decision": "approved"})
        self.assertEqual(status, 303)
        self.assertEqual(server.decisions, {("2026-10-12", "INV-1001", "firm"): "approved"})
        _, html = self.request("GET", "/")
        self.assertIn("Approved", html)

        self.request("POST", "/decide", {"invoice_id": "INV-1001", "step": "firm", "decision": "rejected"})
        self.assertEqual(server.decisions[("2026-10-12", "INV-1001", "firm")], "rejected")

        self.request("POST", "/decide", {"invoice_id": "INV-1001", "step": "firm", "decision": "undo"})
        self.assertEqual(server.decisions, {})

    def test_rejects_invalid_decisions(self):
        server = self.start()
        bad_forms = [
            {"invoice_id": "INV-1002", "step": "friendly", "decision": "approved"},
            {"invoice_id": "INV-9999", "step": "firm", "decision": "approved"},
            {"invoice_id": "INV-1001", "step": "final", "decision": "approved"},
            {"invoice_id": "INV-1001", "step": "firm", "decision": "maybe"},
        ]
        for form in bad_forms:
            with self.subTest(form=form):
                status, _ = self.request("POST", "/decide", form)
                self.assertEqual(status, 400)
        self.assertEqual(server.decisions, {})

    def test_nothing_to_do(self):
        self.start(self.temp_data([], []))
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Nothing to do today", html)

    def test_unreadable_data(self):
        self.start(self.temp_data("[{ broken", []))
        status, html = self.request("GET", "/")
        self.assertEqual(status, 500)
        self.assertIn("could not be read", html)

    def test_missing_data_folder(self):
        self.start(os.path.join(DATA_DIR, "does-not-exist"))
        status, html = self.request("GET", "/")
        self.assertEqual(status, 500)
        self.assertIn("could not be read", html)

    def test_invalid_today(self):
        self.start(today="2026-02-30")
        status, html = self.request("GET", "/")
        self.assertEqual(status, 500)
        self.assertIn("TODAY", html)

    def test_customer_text_is_escaped(self):
        invoices = [{"id": "INV-1", "customer": "<script>alert(1)</script>", "email": "x@test.test",
                     "amount": 10, "currency": "EUR", "due_date": "2026-10-01", "paid_date": None}]
        self.start(self.temp_data(invoices, []))
        _, html = self.request("GET", "/")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_unknown_path(self):
        self.start()
        status, _ = self.request("GET", "/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
