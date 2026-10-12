import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta

from plan import PlanInputError, build_plan, main

TODAY = "2026-10-12"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def days_ago(n):
    return (date.fromisoformat(TODAY) - timedelta(days=n)).isoformat()


def inv(id="INV-1", days=12, **overrides):
    invoice = {
        "id": id, "customer": "Test Ltd", "email": "a@test.test",
        "amount": 100.0, "currency": "EUR", "due_date": days_ago(days), "paid_date": None,
    }
    invoice.update(overrides)
    return invoice


def sent(id, step):
    return {"invoice_id": id, "step": step, "sent_on": "2026-10-01"}


def plan(invoices, sent_list=()):
    return build_plan(list(invoices), list(sent_list), TODAY)


def steps(result):
    return [(a["invoice_id"], a["step"]) for a in result["actions"]]


def reasons(result):
    return [(s["invoice_id"], s["reason"]) for s in result["skipped"]]


class ShapeTest(unittest.TestCase):
    def test_empty_input_gives_empty_plan(self):
        self.assertEqual(build_plan([], [], TODAY), {"today": TODAY, "actions": [], "skipped": []})

    def test_action_has_exact_keys(self):
        action = plan([inv(days=12)])["actions"][0]
        self.assertEqual(set(action), {"invoice_id", "step", "needs_approval", "days_overdue", "message"})

    def test_invalid_today_raises(self):
        for bad in ["2026-13-01", "2026-02-30", "12/10/2026", "", "20261012"]:
            with self.subTest(today=bad), self.assertRaises(PlanInputError):
                build_plan([], [], bad)


class SampleDataTest(unittest.TestCase):
    def test_sample_plan(self):
        with open(os.path.join(DATA_DIR, "invoices.json"), encoding="utf-8") as f:
            invoices = json.load(f)
        with open(os.path.join(DATA_DIR, "sent.json"), encoding="utf-8") as f:
            sent_list = json.load(f)
        result = build_plan(invoices, sent_list, TODAY)
        self.assertEqual(
            [(a["invoice_id"], a["step"], a["needs_approval"], a["days_overdue"]) for a in result["actions"]],
            [
                ("INV-1001", "firm", True, 12),
                ("INV-1002", "friendly", False, 3),
                ("INV-1004", "final", True, 32),
                ("INV-1009", "firm", True, 12),
                ("INV-1011", "firm", True, 10),
            ],
        )
        self.assertEqual(reasons(result), [
            ("INV-1007", "invalid_due_date"),
            ("INV-1008", "missing_due_date"),
            ("INV-1010", "duplicate_id"),
        ])


class ThresholdTest(unittest.TestCase):
    def test_step_boundaries(self):
        cases = [
            (-5, None), (0, None), (2, None),
            (3, "friendly"), (9, "friendly"),
            (10, "firm"), (23, "firm"),
            (24, "final"), (400, "final"),
        ]
        for days, expected in cases:
            with self.subTest(days=days):
                result = plan([inv(days=days)])
                self.assertEqual(steps(result), [("INV-1", expected)] if expected else [])

    def test_days_overdue_and_approval(self):
        for days, approval in [(3, False), (10, True), (24, True)]:
            with self.subTest(days=days):
                action = plan([inv(days=days)])["actions"][0]
                self.assertEqual(action["days_overdue"], days)
                self.assertIs(action["needs_approval"], approval)

    def test_days_counted_across_month_and_year_boundary(self):
        result = build_plan([inv(due_date="2025-12-31")], [], "2026-01-10")
        self.assertEqual(result["actions"][0]["days_overdue"], 10)


class SentTest(unittest.TestCase):
    def test_only_highest_step_is_planned(self):
        self.assertEqual(steps(plan([inv(days=30)])), [("INV-1", "final")])

    def test_lower_step_sent_plans_higher_step(self):
        self.assertEqual(steps(plan([inv(days=12)], [sent("INV-1", "friendly")])), [("INV-1", "firm")])

    def test_same_step_sent_plans_nothing(self):
        self.assertEqual(steps(plan([inv(days=12)], [sent("INV-1", "firm")])), [])

    def test_higher_step_sent_plans_nothing(self):
        self.assertEqual(steps(plan([inv(days=5)], [sent("INV-1", "final")])), [])

    def test_ignored_sent_entries(self):
        sent_list = [
            sent("INV-404", "final"),
            sent("INV-1", "urgent"),
            sent("INV-1", "FIRM"),
            {"invoice_id": "INV-1"},
            {"step": "firm"},
            "not an object",
        ]
        self.assertEqual(steps(plan([inv(days=12)], sent_list)), [("INV-1", "firm")])


class PaidTest(unittest.TestCase):
    def test_paid_before_today_plans_nothing_even_with_reminders(self):
        result = plan([inv(days=30, paid_date=days_ago(2))], [sent("INV-1", "friendly")])
        self.assertEqual(result, {"today": TODAY, "actions": [], "skipped": []})

    def test_paid_today_plans_nothing(self):
        self.assertEqual(plan([inv(days=30, paid_date=TODAY)])["actions"], [])

    def test_missing_paid_date_means_unpaid(self):
        invoice = inv(days=12)
        del invoice["paid_date"]
        self.assertEqual(steps(plan([invoice])), [("INV-1", "firm")])

    def test_invalid_paid_dates(self):
        for bad in ["", "2026-10-13", "2026-02-30", "05/10/2026", "yesterday", 20261001, True, " "]:
            with self.subTest(paid_date=bad):
                self.assertEqual(reasons(plan([inv(paid_date=bad)])), [("INV-1", "invalid_paid_date")])


class BadDataTest(unittest.TestCase):
    def test_duplicate_id_listed_once_and_all_skipped(self):
        result = plan([inv("INV-7", days=30), inv("INV-7", days=5), inv("INV-7", amount=-1), inv("INV-8")])
        self.assertEqual(reasons(result), [("INV-7", "duplicate_id")])
        self.assertEqual(steps(result), [("INV-8", "firm")])

    def test_sent_for_duplicate_id_does_not_matter(self):
        result = plan([inv("INV-7"), inv("INV-7")], [sent("INV-7", "friendly")])
        self.assertEqual(reasons(result), [("INV-7", "duplicate_id")])

    def test_invalid_amounts(self):
        for bad in [0, 0.0, -10, "100", "", None, True, False, [100], {"v": 1}, float("nan"), float("inf")]:
            with self.subTest(amount=bad):
                self.assertEqual(reasons(plan([inv(amount=bad)])), [("INV-1", "invalid_amount")])

    def test_missing_amount(self):
        invoice = inv()
        del invoice["amount"]
        self.assertEqual(reasons(plan([invoice])), [("INV-1", "invalid_amount")])

    def test_integer_and_small_amounts_are_valid(self):
        for good in [100, 0.01, 10 ** 400]:
            with self.subTest(amount=good):
                self.assertEqual(plan([inv(amount=good)])["skipped"], [])

    def test_missing_due_dates(self):
        for bad in [None, ""]:
            with self.subTest(due_date=bad):
                self.assertEqual(reasons(plan([inv(due_date=bad)])), [("INV-1", "missing_due_date")])
        invoice = inv()
        del invoice["due_date"]
        self.assertEqual(reasons(plan([invoice])), [("INV-1", "missing_due_date")])

    def test_invalid_due_dates(self):
        for bad in ["2026-02-30", "30/09/2026", "20260930", "2026-9-30", " 2026-09-30",
                    "2026-09-30T00:00:00", "2026-W40-1", "abc", " ", 20260930, True]:
            with self.subTest(due_date=bad):
                self.assertEqual(reasons(plan([inv(due_date=bad)])), [("INV-1", "invalid_due_date")])

    def test_leap_day_is_a_real_date(self):
        result = build_plan([inv(due_date="2028-02-29")], [], "2028-03-10")
        self.assertEqual(steps(result), [("INV-1", "firm")])

    def test_first_reason_wins(self):
        cases = [
            ([inv("A", amount=0), inv("A", due_date=None)], "duplicate_id"),
            ([inv("A", amount=0, due_date=None)], "invalid_amount"),
            ([inv("A", due_date="", paid_date="")], "missing_due_date"),
            ([inv("A", due_date="2026-02-30", paid_date="bad")], "invalid_due_date"),
        ]
        for invoices, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(reasons(plan(invoices)), [("A", expected)])

    def test_bad_data_wins_over_paid(self):
        result = plan([inv(amount=-5, paid_date=days_ago(1))])
        self.assertEqual(reasons(result), [("INV-1", "invalid_amount")])


class SortTest(unittest.TestCase):
    def test_sorted_character_by_character(self):
        result = plan([inv("INV-99"), inv("INV-100"), inv("INV-1000"), inv("INV-2")])
        self.assertEqual([a["invoice_id"] for a in result["actions"]],
                         ["INV-100", "INV-1000", "INV-2", "INV-99"])

    def test_skipped_sorted(self):
        result = plan([inv("B", amount=0), inv("A", due_date=None), inv("C"), inv("C")])
        self.assertEqual([s["invoice_id"] for s in result["skipped"]], ["A", "B", "C"])


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.invoices = self.write("invoices.json", [inv(days=12)])
        self.sent = self.write("sent.json", [])
        self.out = os.path.join(self.tmp.name, "actions.json")

    def write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def run_main(self, invoices=None, sent_path=None, today=TODAY):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(["--invoices", invoices or self.invoices, "--sent", sent_path or self.sent,
                         "--today", today, "--out", self.out])
        return code, err.getvalue()

    def test_writes_plan_and_exits_zero(self):
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        with open(self.out, encoding="utf-8") as f:
            result = json.load(f)
        self.assertEqual(steps(result), [("INV-1", "firm")])

    def test_invalid_today(self):
        code, err = self.run_main(today="2026-13-01")
        self.assertNotEqual(code, 0)
        self.assertIn("--today", err)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_file(self):
        code, err = self.run_main(invoices=os.path.join(self.tmp.name, "nope.json"))
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err)

    def test_invalid_json(self):
        code, err = self.run_main(sent_path=self.write("broken.json", "[{"))
        self.assertNotEqual(code, 0)
        self.assertIn("not valid JSON", err)

    def test_not_a_list(self):
        code, err = self.run_main(invoices=self.write("obj.json", {"id": "INV-1"}))
        self.assertNotEqual(code, 0)
        self.assertIn("list", err)


if __name__ == "__main__":
    unittest.main()
