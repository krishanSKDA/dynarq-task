import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date

from messages import build_message

STEPS = [
    ("friendly", 3, False),
    ("firm", 10, True),
    ("final", 24, True),
]
STEP_RANK = {name: rank for rank, (name, _, _) in enumerate(STEPS)}
NEEDS_APPROVAL = {name: approval for name, _, approval in STEPS}

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class PlanInputError(Exception):
    pass


def parse_date(value):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_valid_amount(amount):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return False
    return math.isfinite(amount) and amount > 0


def skip_reason(invoice, today, duplicate_ids):
    if invoice.get("id") in duplicate_ids:
        return "duplicate_id"
    if not _is_valid_amount(invoice.get("amount")):
        return "invalid_amount"
    due = invoice.get("due_date")
    if due is None or due == "":
        return "missing_due_date"
    if parse_date(due) is None:
        return "invalid_due_date"
    paid = invoice.get("paid_date")
    if paid is not None:
        paid_on = parse_date(paid)
        if paid_on is None or paid_on > today:
            return "invalid_paid_date"
    return None


def step_for(days_overdue):
    reached = None
    for name, min_days, _ in STEPS:
        if days_overdue >= min_days:
            reached = name
    return reached


def sent_steps_by_invoice(sent):
    result = {}
    for entry in sent:
        if not isinstance(entry, dict):
            continue
        step = entry.get("step")
        invoice_id = entry.get("invoice_id")
        if step in STEP_RANK and isinstance(invoice_id, str):
            result.setdefault(invoice_id, set()).add(step)
    return result


def build_plan(invoices, sent, today):
    today_date = parse_date(today)
    if today_date is None:
        raise PlanInputError(f"today must be a real date as YYYY-MM-DD, got {today!r}")

    id_counts = Counter(inv.get("id") for inv in invoices)
    duplicate_ids = {invoice_id for invoice_id, n in id_counts.items() if n > 1}
    already_sent = sent_steps_by_invoice(sent)

    actions = []
    skipped = []
    listed_duplicates = set()

    for invoice in invoices:
        invoice_id = invoice.get("id")

        reason = skip_reason(invoice, today_date, duplicate_ids)
        if reason == "duplicate_id":
            if invoice_id not in listed_duplicates:
                listed_duplicates.add(invoice_id)
                skipped.append({"invoice_id": invoice_id, "reason": reason})
            continue
        if reason:
            skipped.append({"invoice_id": invoice_id, "reason": reason})
            continue

        if invoice.get("paid_date") is not None:
            continue

        days_overdue = (today_date - parse_date(invoice["due_date"])).days
        step = step_for(days_overdue)
        if step is None:
            continue

        sent_ranks = [STEP_RANK[s] for s in already_sent.get(invoice_id, ())]
        if sent_ranks and max(sent_ranks) >= STEP_RANK[step]:
            continue

        actions.append({
            "invoice_id": invoice_id,
            "step": step,
            "needs_approval": NEEDS_APPROVAL[step],
            "days_overdue": days_overdue,
            "message": build_message(step, invoice, days_overdue),
        })

    actions.sort(key=lambda a: a["invoice_id"])
    skipped.sort(key=lambda s: s["invoice_id"])
    return {"today": today, "actions": actions, "skipped": skipped}


def read_json_list(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise PlanInputError(f"{label} file not found: {path}")
    except OSError as e:
        raise PlanInputError(f"cannot read {label} file {path}: {e.strerror or e}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PlanInputError(f"{label} file {path} is not valid JSON: {e}")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise PlanInputError(f"{label} file {path} must be a JSON list of objects")
    return data


def plan_from_files(invoices_path, sent_path, today):
    invoices = read_json_list(invoices_path, "invoices")
    sent = read_json_list(sent_path, "sent reminders")
    return build_plan(invoices, sent, today), invoices


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan today's payment reminders.")
    parser.add_argument("--invoices", required=True, help="path to invoices.json")
    parser.add_argument("--sent", required=True, help="path to sent.json")
    parser.add_argument("--today", required=True, help="date as YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="path to write the plan JSON")
    args = parser.parse_args(argv)

    try:
        if parse_date(args.today) is None:
            raise PlanInputError(f"--today must be a real date as YYYY-MM-DD, got {args.today!r}")
        plan, _ = plan_from_files(args.invoices, args.sent, args.today)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except PlanInputError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Error: cannot write {args.out}: {e.strerror or e}", file=sys.stderr)
        return 1

    print(f"Wrote {len(plan['actions'])} action(s) and {len(plan['skipped'])} skipped "
          f"invoice(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
