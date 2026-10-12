# Payment reminder assistant (Python)

Python 3.11+, standard library only, nothing to install. Run everything from inside `python/`.

- **Tests:** `python -m unittest` (rules, messages, command and page; 53 tests)
- **Command:** `python plan.py --invoices ../data/invoices.json --sent ../data/sent.json --today 2026-10-12 --out ../actions.json`
- **Page:** `python app.py`, then open http://localhost:8000/ (env: `PORT`, `DATA_DIR` default `../data`, `TODAY` default real date)

## Decisions

- `plan.py` follows the rules in their written order: `skip_reason` (first bad-data reason), paid, `step_for` (highest step reached), then the already-sent check. One `STEPS` table holds the thresholds and approval flags.
- Dates are strict: a `YYYY-MM-DD` regex, then `date.fromisoformat`, because `fromisoformat` alone accepts `20260930` and `2026-W40-1`. `true`, strings, NaN and Infinity are not valid amounts.
- A bad `--today`, a missing file, invalid JSON or a file that is not a list prints `Error: ...` and exits 1. Malformed or unknown entries in `sent.json` are ignored.
- Messages (`messages.py`) are fixed templates per step, so they work with no key and no internet. `check_message` encodes the content rules (all required parts present, no other figure, no fee/interest/earlier-reminder wording), and the tests run it on every template.
- The page uses `http.server` with no framework, and calls `plan_from_files` from the engine, so the rules exist once. Decisions are kept in memory per (date, invoice, step), with undo. A POST is accepted only for a firm/final action in today's plan. All data is HTML-escaped.
- The page is laid out as cards that fit 390px, uses native buttons and forms for keyboard use, shows a "Nothing to do today" state, and shows a plain-language error page when the data cannot be read.
- `RESEARCH.md` uses only `sources/`. The owner's 25 EUR "maximum" and "reminder needed for interest" beliefs are contradicted by Articles 6 and 3.

## Next, with more time

- Store decisions (for example in SQLite) with who decided and when, and append to `sent.json` once a reminder is really sent.
- Optional AI-written messages, used only when `check_message` passes, otherwise the template.
- Run an automated accessibility check (axe) and a screen-reader pass on the page; let the owner edit templates.
