import re
from decimal import ROUND_HALF_UP, Decimal

TEMPLATES = {
    "friendly": (
        "Dear {customer},\n\n"
        "This is a friendly reminder that invoice {invoice_id} for {amount}, "
        "due on {due_date}, is now {days} days overdue.\n\n"
        "If you have already paid, please disregard this message. Otherwise we would "
        "be grateful if you could arrange payment at your earliest convenience.\n\n"
        "Kind regards"
    ),
    "firm": (
        "Dear {customer},\n\n"
        "Our records show that invoice {invoice_id} for {amount}, due on {due_date}, "
        "is still unpaid and is now {days} days overdue.\n\n"
        "Please arrange payment promptly. If there is a problem with this invoice, "
        "please contact us so we can resolve it.\n\n"
        "Regards"
    ),
    "final": (
        "Dear {customer},\n\n"
        "Final notice: invoice {invoice_id} for {amount}, due on {due_date}, "
        "is still unpaid and is now {days} days overdue.\n\n"
        "Please pay the full amount immediately. If you cannot pay, contact us "
        "without delay so we can discuss this invoice.\n\n"
        "Regards"
    ),
}

FORBIDDEN_WORDS = ("fee", "interest", "penalty", "charge", "compensation", "%",
                   "previous", "earlier", "already sent", "last reminder")


def format_amount(amount, currency):
    value = Decimal(repr(float(amount))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{currency} {value:,.2f}"


def build_message(step, invoice, days_overdue):
    if step not in TEMPLATES:
        raise ValueError(f"unknown step: {step!r}")
    return TEMPLATES[step].format(
        customer=invoice["customer"],
        invoice_id=invoice["id"],
        amount=format_amount(invoice["amount"], invoice["currency"]),
        due_date=invoice["due_date"],
        days=days_overdue,
    )


def check_message(message, invoice, days_overdue):
    required = {
        "customer name": str(invoice["customer"]),
        "invoice id": str(invoice["id"]),
        "due date": str(invoice["due_date"]),
        "amount": format_amount(invoice["amount"], invoice["currency"]),
        "days overdue": f"{days_overdue} days",
    }
    problems = [f"missing {name}" for name, text in required.items() if text not in message]

    rest = message
    for text in sorted(required.values(), key=len, reverse=True):
        rest = rest.replace(text, " ")
    if re.search(r"\d", rest):
        problems.append("contains a figure that is not from the invoice")

    lowered = rest.lower()
    problems += [f"mentions {word!r}" for word in FORBIDDEN_WORDS if word in lowered]
    return problems
