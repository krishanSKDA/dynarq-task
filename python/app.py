import os
import sys
import threading
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote

from messages import format_amount
from plan import PlanInputError, plan_from_files

REASON_TEXT = {
    "duplicate_id": "The same invoice number appears more than once in the invoice list, "
                    "so none of those invoices were checked.",
    "invalid_amount": "The amount is missing, is not a number, or is zero or less.",
    "missing_due_date": "The invoice has no due date.",
    "invalid_due_date": "The due date is not a real date in the form YYYY-MM-DD.",
    "invalid_paid_date": "The payment date is not a real date, or it is later than today.",
}

STEP_TEXT = {
    "friendly": "Friendly reminder",
    "firm": "Firm reminder",
    "final": "Final notice",
}

DECISION_TEXT = {
    "approved": "Approved",
    "rejected": "Rejected",
}

CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --text: #1b1f24; --muted: #57606a; --border: #d0d7de;
  --accent: #0b5cad; --ok-bg: #e6f4ea; --ok: #1a6b34; --bad-bg: #fdecea; --bad: #a4251c;
  --warn-bg: #fff4e0; --warn: #7a4a00; --focus: #f0a400;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --card: #181c22; --text: #e6e8eb; --muted: #a0a8b2; --border: #343b45;
    --accent: #6cb0ff; --ok-bg: #133321; --ok: #7fd69a; --bad-bg: #3a1714; --bad: #ff9a8f;
    --warn-bg: #3a2a0c; --warn: #ffcb6b;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.6rem; margin: 8px 0 4px; }
h2 { font-size: 1.25rem; margin: 32px 0 12px; }
h3 { font-size: 1.1rem; margin: 0; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 16px; top: 8px; background: var(--card); padding: 8px; z-index: 1; }
a { color: var(--accent); }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.summary { display: flex; flex-wrap: wrap; gap: 8px; padding: 0; list-style: none; margin: 12px 0 0; }
.summary li { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin: 0 0 16px; }
.card-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: baseline; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px 16px; margin: 12px 0; }
.facts div { min-width: 0; }
.facts dt { font-size: .85rem; color: var(--muted); }
.facts dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
.badge { display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: .85rem; font-weight: 600;
  border: 1px solid var(--border); }
.step-friendly { background: var(--ok-bg); color: var(--ok); }
.step-firm { background: var(--warn-bg); color: var(--warn); }
.step-final { background: var(--bad-bg); color: var(--bad); }
.message { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg); border-left: 4px solid var(--border);
  margin: 0; padding: 12px; border-radius: 4px; }
details summary { cursor: pointer; padding: 4px 0; }
.decision { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.status { font-weight: 600; margin-right: auto; }
.status-pending { color: var(--warn); }
.status-approved { color: var(--ok); }
.status-rejected { color: var(--bad); }
form { margin: 0; }
button { font: inherit; min-height: 44px; padding: 8px 16px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--border); background: var(--card); color: var(--text); }
button.approve { background: var(--ok); color: var(--card); border-color: var(--ok); }
button.reject { background: var(--bad); color: var(--card); border-color: var(--bad); }
.notice { background: var(--card); border: 1px solid var(--border); border-left: 6px solid var(--accent);
  border-radius: 8px; padding: 16px; }
.notice.error { border-left-color: var(--bad); }
.skipped { list-style: none; padding: 0; margin: 0; }
.skipped li { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 8px; }
code { overflow-wrap: anywhere; }
"""


def page(title, body):
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body>"
        "<a class=\"skip\" href=\"#content\">Skip to content</a>"
        f"<main id=\"content\">{body}</main></body></html>"
    )


def anchor(invoice_id):
    return "inv-" + quote(str(invoice_id), safe="")


def render_action(action, invoice, decision):
    invoice_id = action["invoice_id"]
    step = action["step"]
    amount = format_amount(invoice["amount"], invoice["currency"])
    days = action["days_overdue"]

    if action["needs_approval"]:
        status_key = decision or "pending"
        status = DECISION_TEXT.get(decision, "Waiting for your decision")
        buttons = ""
        for value, label, css in [("approved", "Approve", "approve"), ("rejected", "Reject", "reject")]:
            if decision != value:
                buttons += (
                    f"<form method=\"post\" action=\"/decide\">"
                    f"<input type=\"hidden\" name=\"invoice_id\" value=\"{escape(invoice_id)}\">"
                    f"<input type=\"hidden\" name=\"step\" value=\"{escape(step)}\">"
                    f"<input type=\"hidden\" name=\"decision\" value=\"{value}\">"
                    f"<button type=\"submit\" class=\"{css}\" "
                    f"aria-label=\"{label} {escape(STEP_TEXT[step].lower())} for {escape(invoice_id)}\">"
                    f"{label}</button></form>"
                )
        if decision:
            buttons += (
                f"<form method=\"post\" action=\"/decide\">"
                f"<input type=\"hidden\" name=\"invoice_id\" value=\"{escape(invoice_id)}\">"
                f"<input type=\"hidden\" name=\"step\" value=\"{escape(step)}\">"
                f"<input type=\"hidden\" name=\"decision\" value=\"undo\">"
                f"<button type=\"submit\" aria-label=\"Undo decision for {escape(invoice_id)}\">Undo</button></form>"
            )
        decision_html = (
            f"<div class=\"decision\"><p class=\"status status-{status_key}\" role=\"status\">"
            f"{escape(status)}</p>{buttons}</div>"
        )
    else:
        decision_html = "<div class=\"decision\"><p class=\"status muted\">No approval needed</p></div>"

    return (
        f"<article class=\"card\" id=\"{anchor(invoice_id)}\" aria-labelledby=\"{anchor(invoice_id)}-h\">"
        f"<div class=\"card-head\"><h3 id=\"{anchor(invoice_id)}-h\">{escape(str(invoice['customer']))}</h3>"
        f"<span class=\"badge step-{escape(step)}\">{escape(STEP_TEXT[step])}</span></div>"
        "<dl class=\"facts\">"
        f"<div><dt>Invoice</dt><dd>{escape(invoice_id)}</dd></div>"
        f"<div><dt>Amount</dt><dd>{escape(amount)}</dd></div>"
        f"<div><dt>Due date</dt><dd>{escape(str(invoice['due_date']))}</dd></div>"
        f"<div><dt>Days overdue</dt><dd>{days}</dd></div>"
        "</dl>"
        f"<details open><summary>Message to the customer</summary>"
        f"<p class=\"message\">{escape(action['message'])}</p></details>"
        f"{decision_html}</article>"
    )


def render_plan(plan, invoices, decisions):
    today = plan["today"]
    invoices_by_id = {inv.get("id"): inv for inv in invoices}
    actions = plan["actions"]
    skipped = plan["skipped"]

    approval = [a for a in actions if a["needs_approval"]]
    waiting = [a for a in approval if (today, a["invoice_id"], a["step"]) not in decisions]

    body = f"<h1>Payment reminders</h1><p class=\"muted\">Plan for {escape(today)}</p>"
    body += (
        "<ul class=\"summary\">"
        f"<li><strong>{len(actions)}</strong> reminder{'s' if len(actions) != 1 else ''} today</li>"
        f"<li><strong>{len(waiting)}</strong> waiting for your approval</li>"
        f"<li><strong>{len(skipped)}</strong> invoice{'s' if len(skipped) != 1 else ''} could not be checked</li>"
        "</ul>"
    )

    body += "<h2>Today's reminders</h2>"
    if actions:
        body += ("<p class=\"muted\">Friendly reminders need no approval. Firm reminders and final notices "
                 "are only recorded after you approve them. Nothing is sent from this page.</p>")
        for action in actions:
            invoice = invoices_by_id[action["invoice_id"]]
            decision = decisions.get((today, action["invoice_id"], action["step"]))
            body += render_action(action, invoice, decision)
    else:
        body += ("<div class=\"notice\"><p><strong>Nothing to do today.</strong> No invoice needs a reminder: "
                 "every invoice is either paid, not yet 3 days overdue, or has already had its reminder.</p></div>")

    body += "<h2>Invoices that could not be checked</h2>"
    if skipped:
        body += "<p class=\"muted\">Fix these in the invoice data; they get no reminder until then.</p><ul class=\"skipped\">"
        for item in skipped:
            text = REASON_TEXT.get(item["reason"], "The invoice data has a problem.")
            body += f"<li><strong>{escape(str(item['invoice_id']))}</strong>: {escape(text)}</li>"
        body += "</ul>"
    else:
        body += "<p>None. All invoice data could be read.</p>"

    return page("Payment reminders", body)


def render_error(detail, data_dir):
    body = (
        "<h1>Payment reminders</h1>"
        "<div class=\"notice error\" role=\"alert\">"
        "<h2>The invoice data could not be read</h2>"
        "<p>No plan can be shown until this is fixed. Nothing has been sent.</p>"
        f"<p>Details: <code>{escape(detail)}</code></p>"
        f"<p>Check that <code>invoices.json</code> and <code>sent.json</code> exist in "
        f"<code>{escape(os.path.abspath(data_dir))}</code> and are valid JSON lists, "
        "and that TODAY (if set) is a date like 2026-10-12. Then reload this page.</p>"
        "</div>"
    )
    return page("Payment reminders: data problem", body)


class Handler(BaseHTTPRequestHandler):
    def today(self):
        return self.server.today or date.today().isoformat()

    def load(self):
        data_dir = self.server.data_dir
        return plan_from_files(
            os.path.join(data_dir, "invoices.json"),
            os.path.join(data_dir, "sent.json"),
            self.today(),
        )

    def send_html(self, status, html):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] != "/":
            self.send_html(404, page("Not found", "<h1>Page not found</h1><p><a href=\"/\">Go to today's plan</a></p>"))
            return
        try:
            plan, invoices = self.load()
        except PlanInputError as e:
            self.send_html(500, render_error(str(e), self.server.data_dir))
            return
        with self.server.lock:
            decisions = dict(self.server.decisions)
        self.send_html(200, render_plan(plan, invoices, decisions))

    def do_POST(self):
        if self.path != "/decide":
            self.send_html(404, page("Not found", "<h1>Page not found</h1>"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        invoice_id = form.get("invoice_id", [""])[0]
        step = form.get("step", [""])[0]
        decision = form.get("decision", [""])[0]

        try:
            plan, _ = self.load()
        except PlanInputError as e:
            self.send_html(500, render_error(str(e), self.server.data_dir))
            return

        action = next((a for a in plan["actions"] if a["invoice_id"] == invoice_id and a["step"] == step), None)
        if action is None or not action["needs_approval"] or decision not in ("approved", "rejected", "undo"):
            self.send_html(400, page("Cannot record decision",
                                     "<h1>That decision cannot be recorded</h1>"
                                     "<p>This reminder is not in today's plan or does not need approval.</p>"
                                     "<p><a href=\"/\">Back to today's plan</a></p>"))
            return

        key = (plan["today"], invoice_id, step)
        with self.server.lock:
            if decision == "undo":
                self.server.decisions.pop(key, None)
            else:
                self.server.decisions[key] = decision

        self.send_response(303)
        self.send_header("Location", f"/#{anchor(invoice_id)}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def make_server(port, data_dir, today=None):
    server = ThreadingHTTPServer(("", port), Handler)
    server.data_dir = data_dir
    server.today = today
    server.decisions = {}
    server.lock = threading.Lock()
    return server


def main():
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        print(f"Error: PORT must be a number, got {os.environ.get('PORT')!r}", file=sys.stderr)
        return 1
    data_dir = os.environ.get("DATA_DIR", "../data")
    today = os.environ.get("TODAY") or None
    server = make_server(port, data_dir, today)
    print(f"Serving on http://localhost:{port}/ (data: {os.path.abspath(data_dir)}, "
          f"today: {today or 'real date'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
