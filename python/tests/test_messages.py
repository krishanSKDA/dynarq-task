import unittest

from messages import TEMPLATES, build_message, check_message, format_amount
from plan import build_plan

INVOICE = {
    "id": "INV-1001", "customer": "Alder Fabrication Ltd", "email": "a@test.test",
    "amount": 1250.0, "currency": "EUR", "due_date": "2026-09-30", "paid_date": None,
}


class FormatAmountTest(unittest.TestCase):
    def test_two_decimals_with_currency(self):
        cases = [
            (1250.0, "EUR", "EUR 1,250.00"),
            (215.5, "EUR", "EUR 215.50"),
            (845.25, "USD", "USD 845.25"),
            (100, "GBP", "GBP 100.00"),
            (0.125, "EUR", "EUR 0.13"),
            (1234567.891, "EUR", "EUR 1,234,567.89"),
            (1e30, "EUR", "EUR 1,000,000,000,000,000,000,000,000,000,000.00"),
            (10 ** 30, "EUR", "EUR 1,000,000,000,000,000,000,000,000,000,000.00"),
        ]
        for amount, currency, expected in cases:
            with self.subTest(amount=amount):
                self.assertEqual(format_amount(amount, currency), expected)


class MessageTest(unittest.TestCase):
    def test_every_step_passes_the_rules(self):
        for step in TEMPLATES:
            with self.subTest(step=step):
                message = build_message(step, INVOICE, 12)
                self.assertEqual(check_message(message, INVOICE, 12), [])
                for part in ["Alder Fabrication Ltd", "INV-1001", "2026-09-30", "EUR 1,250.00", "12 days"]:
                    self.assertIn(part, message)

    def test_tone_differs_by_step(self):
        self.assertIn("friendly reminder", build_message("friendly", INVOICE, 3))
        self.assertIn("Please arrange payment promptly", build_message("firm", INVOICE, 12))
        self.assertIn("Final notice", build_message("final", INVOICE, 30))

    def test_digits_in_customer_name_are_allowed(self):
        invoice = dict(INVOICE, customer="Unit 4 Studio 2026")
        message = build_message("firm", invoice, 12)
        self.assertEqual(check_message(message, invoice, 12), [])

    def test_unknown_step(self):
        with self.assertRaises(ValueError):
            build_message("urgent", INVOICE, 12)

    def test_plan_messages_pass_the_rules(self):
        invoices = [
            dict(INVOICE, id="A", due_date="2026-10-09"),
            dict(INVOICE, id="B", due_date="2026-10-01", amount=99.999, currency="USD"),
            dict(INVOICE, id="C", due_date="2026-08-01", amount=7),
        ]
        by_id = {i["id"]: i for i in invoices}
        result = build_plan(invoices, [], "2026-10-12")
        self.assertEqual(len(result["actions"]), 3)
        for action in result["actions"]:
            with self.subTest(invoice=action["invoice_id"]):
                problems = check_message(action["message"], by_id[action["invoice_id"]], action["days_overdue"])
                self.assertEqual(problems, [])


class CheckMessageTest(unittest.TestCase):
    def good(self):
        return build_message("firm", INVOICE, 12)

    def test_rejects_extra_figure(self):
        message = self.good() + " Please pay within 7 days."
        self.assertIn("contains a figure that is not from the invoice", check_message(message, INVOICE, 12))

    def test_rejects_fees_and_interest(self):
        message = self.good() + " A late fee and interest will be added."
        problems = check_message(message, INVOICE, 12)
        self.assertIn("mentions 'fee'", problems)
        self.assertIn("mentions 'interest'", problems)

    def test_rejects_mention_of_earlier_reminders(self):
        message = self.good() + " As in our previous email."
        self.assertIn("mentions 'previous'", check_message(message, INVOICE, 12))

    def test_rejects_missing_parts(self):
        problems = check_message("Dear customer, please pay.", INVOICE, 12)
        for name in ["customer name", "invoice id", "due date", "amount", "days overdue"]:
            self.assertIn(f"missing {name}", problems)


if __name__ == "__main__":
    unittest.main()
