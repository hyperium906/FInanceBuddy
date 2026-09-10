# FinanceBuddy

A personal finance dashboard built with Streamlit, backed by a Google Sheet.

Six pages: a **Dashboard** of headline numbers, a **Budget** page with paycheck
allocation and a daily burn rate, a searchable **Transactions** ledger, a CSV
**Import** with automatic categorization, a **Wishlist**, and a **Buy Advisor**
that tells you whether a purchase is a good idea — with the arithmetic done in
Python, not by a model.

---

## Setup

### 1. Install

```bash
git clone <your-repo> && cd FinanceBuddy
python3 -m venv .venv && source .venv/bin/activate
pip install -r finance_app/requirements.txt
```

### 2. Google service account

The app talks to your sheet as a *service account* — a robot Google user with
its own email address. Nothing is shared publicly.

1. Open the [Google Cloud console](https://console.cloud.google.com/) and
   create a project (or pick an existing one).
2. **APIs & Services → Library** → enable both:
   - **Google Sheets API**
   - **Google Drive API**
3. **APIs & Services → Credentials → Create credentials → Service account.**
   Give it any name; no roles are needed.
4. Open the new service account → **Keys → Add key → Create new key → JSON**.
   A `.json` file downloads. Keep it — you cannot re-download it.
5. Move that file into the project and point `GOOGLE_CREDS_PATH` at it. It is
   already covered by `.gitignore`; **never commit it.**
6. Open the JSON and copy the `client_email` value — it looks like
   `something@your-project.iam.gserviceaccount.com`.
7. Open your Google Sheet → **Share** → paste that email → give it **Editor** →
   Send. This is the step people forget; without it every read fails with a
   permission error.
8. Copy the sheet's ID from its URL — the long string between `/d/` and `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

### 3. Gemini API key

Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).
The free tier is enough; it allows roughly 10-15 requests per minute, which the
app backs off around automatically.

### 4. Environment

```bash
cp finance_app/.env.example .env
```

Fill it in:

| Variable | What it is |
| --- | --- |
| `GOOGLE_SHEET_ID` | The ID from the sheet URL (step 8) |
| `GOOGLE_CREDS_PATH` | Path to the service-account JSON (step 4) |
| `GEMINI_API_KEY` | Your AI Studio key |
| `GEMINI_MODEL` | Model for the Buy Advisor's explanation, e.g. `gemini-2.5-pro` |
| `GEMINI_MODEL_FAST` | Model for bulk categorizing, e.g. `gemini-2.5-flash` |

Model names are configuration, never hardcoded. Missing variables raise a
`ConfigError` at startup naming every one of them.

### 5. Run

```bash
streamlit run finance_app/app.py
```

On first run the app checks your workbook. **If a tab is missing it shows a
setup screen listing exactly what to create, with the header row to paste** —
it does not crash. Create the tabs, press Re-check, and carry on.

---

## Sheet tabs

Two kinds of tab, and the difference matters:

- **Data tabs** are prefixed with `_`. They are plain tables with a header row.
  The app reads and writes **only** these.
- **`Budget Sheet`** is your human-readable report, driven by formulas that pull
  from the underscore tabs. **The app never writes to it** — a guard raises
  before any write to a tab without the `_` prefix.

Paste the headers below into row 1 of each tab. Names are case-sensitive.

| Tab | Header row |
| --- | --- |
| `_Accounts` | `Account ID` · `Name` · `Type` · `Institution` · `Balance` · `Currency` · `Last Updated` |
| `_Transactions` | `Transaction ID` · `Date` · `Account ID` · `Description` · `Category` · `Amount` · `Notes` |
| `_Recurring` | `Recurring ID` · `Name` · `Category` · `Amount` · `Frequency` · `Next Due` · `Account ID` · `Active` |
| `_Budgets` | `Budget ID` · `Month` · `Category` · `Amount` · `Notes` |
| `_Allocations` | `Allocation ID` · `Month` · `Bucket` · `Percent` · `Amount` · `Account ID` · `Notes` |
| `_Debts` | `Debt ID` · `Name` · `Type` · `Balance` · `APR` · `Minimum Payment` · `Due Day` · `Account ID` |
| `_Wishlist` | `Item ID` · `Name` · `Price` · `URL` · `Category` · `Priority` · `Status` · `Added On` · `Notes` · `Target Date` |
| `_Goals` | `Goal ID` · `Name` · `Target Amount` · `Saved Amount` · `Target Date` · `Bucket` · `Account ID` · `Notes` |
| `_Config` | `Key` · `Value` |

Notes:

- `_Allocations` does double duty. Rows with a **blank `Month`** are your
  standing paycheck rules; rows **with** a month are recorded history and feed
  the goal-pace calculation.
- `Percent` is in percent points: `10` means 10%.
- `_Config` is free-form key/value. Recognised keys:

| Key | Effect |
| --- | --- |
| `paycheck_amount` | Your take-home per paycheck. Drives plan health, surplus, and the advisor. |
| `pay_anchor_date` | Any known payday, `YYYY-MM-DD`. Bi-weekly dates are derived from it. |
| `monthly_savings_target` | Overrides the goal-derived savings target. |
| `rollover_enabled` | `true` to carry unspent category budget into next month. |

---

## How each page works

### Dashboard

- **Safe to spend** = cash − bills still due this month − (savings target −
  already allocated, floored at zero).
- **Month-over-month deltas** are reconstructed by rewinding transactions from
  today's balances, since `_Accounts` stores only the current figure. An account
  with no transaction history is reported unchanged rather than guessed at.
- **Budget bars**: green below 80%, amber at or past 80%, red only when
  *strictly* over. Exactly 100% is amber — spent, not overspent.
- **Goal pace** is the 3-month average of `_Allocations` for the goal's bucket.
  With no history the verdict is *unknown*, never "behind".
- A **third paycheck** in the month appears as a banner with buttons to send it
  to a goal, a wishlist item, or debt.

### Budget

Bi-weekly means **26 paychecks a year, not 24** — every 14 days from
`pay_anchor_date`. The plan deliberately assumes **two** a month as the
conservative baseline; roughly twice a year a month holds a third, surfaced on
the Dashboard rather than folded into the plan.

Percent rules are computed **first and against the full paycheck**, so a 10%
tithe scales when pay changes; fixed dollar rules follow. Rules are never
silently capped — if they claim more than the paycheck holds, the remainder goes
negative and says so.

**Remaining per day** divides what is left by days left in the month (today
counts as spendable, so it never divides by zero). **Rollover** carries an
unspent category balance forward; an overspent category carries zero, never a
debt.

### Transactions

Everything already in `_Transactions`, filtered down to what you want to look
at: a period, an account, a category, money in or out, an amount band, or a
free-text search across descriptions and notes. The search is matched
literally, so a merchant name containing `*` or `(` is not a broken pattern.
The totals above the table are computed from the **filtered** rows, so the
headline figures always agree with what is underneath them.

**Category, Notes, and Account ID are editable; Date, Description, and Amount
are not.** Those three are what the bank reported, and a browser that lets you
quietly rewrite them makes the sheet disagree with the statement it came from —
a guard in `update_transactions` refuses them. Correcting a category here
teaches a rule exactly as the Import page does, so the fix pays forward.

Saving is batched: every edited cell across every edited row goes out in one
`batch_update` after one read. Rows are resolved before anything is written, so
a stale ID aborts the whole save rather than half-applying it.

Two things sit below the table. **Where it went** nets refunds against their
own category, so a fully refunded purchase disappears rather than showing up as
income. **Possible duplicates** flags rows sharing a date, amount, and
description — the shape a double import takes. Two coffees on one day look
identical too, so it only ever flags them; deleting is done in the sheet.

### Import

Categorization runs in two tiers, cheapest first:

1. **Rules** — `category_rules.json`, 95 starter merchants across your
   categories. Patterns match whole tokens, so `rent` will not fire on "PARENT".
2. **The model** — only rows no rule matched, batched 40 at a time to
   `GEMINI_MODEL_FAST`, constrained by a response schema to the category list.
   429s back off 1s/2s/4s/8s; a batch that still fails leaves those rows
   `Uncategorized` rather than failing the import.

Only a **scrubbed merchant name and the amount** are sent. Correcting a category
in the preview writes a new rule, so that merchant is free next time.

### Wishlist

A product URL is auto-filled from JSON-LD `Product` schema, then
microdata/RDFa, then OpenGraph, then meta price tags. **Auto-fill failing is
normal** — large retailers block scrapers. The fields stay blank to type into
and a small note explains why. Fetches are cached for an hour.

### Buy Advisor

**The model does not compute numbers.** Verdicts come from ordered rules against
thresholds at the top of `logic/affordability.py`, all editable:

| Constant | Meaning |
| --- | --- |
| `TIGHT_REMAINING_RATIO` | Leaves under this share of discretionary → "tight" |
| `TIGHT_REMAINING_FLOOR` | …or under this many dollars |
| `MAX_WAIT_DAYS` | Longer than this to afford → "not advised", not "wait" |
| `FINANCE_OUTFLOW_CEILING` | Commitments + payment must stay under this share of income |
| `GOAL_DELAY_TOLERANCE_DAYS` | A goal slipping more than this is material |

- **Goal delay** accrues only on the amount that *overruns* discretionary money.
  A purchase covered by discretionary cash delays nothing.
- **Financing assumes 0% interest.** A real offer with an APR costs more.
- **Days to afford** is `None` when there is no surplus — "never at this rate",
  never a fabricated date.

If the API fails or rate-limits, the verdict and the full numbers table still
render; only the prose is missing.

### Voice input (optional)

The advisor's query box has a 🎤 button: it records a clip in the browser and
transcribes it **locally** with faster-whisper, so no audio leaves your machine.
The transcript lands in the box as **editable text and is never auto-submitted**.

```bash
pip install audio-recorder-streamlit faster-whisper
```

First use downloads the `base` model to `~/.cache/huggingface/` — **about
141 MB**, roughly 40 seconds including the download; later calls take a second
or two. Set `WHISPER_MODEL` to `tiny` (~75 MB) or `small` (~460 MB) for a
different trade-off.

**Both packages are optional.** Without them the button becomes a greyed
"🎤 off" marker whose tooltip says what to install, and typing works normally.

---

## Tests

```bash
pytest
```

217 tests, all offline — no API key, no network, no quota, no Google account.
`tests/conftest.py` holds synthetic DataFrames; `tests/checks/` holds
longer narrative regression scripts that `pytest` runs as subprocesses.

Covered edge cases include a zero-income month, negative and overdrawn
balances, a category with no budget set, an item priced above all available
cash, and a month with no transactions at all.

---

## Swapping the CSV importer for a bank feed

The Import page is deliberately thin. `pages_ui/import_preview.py` does three
separable things:

1. **Acquire** rows — currently `pd.read_csv` on an upload.
2. **Normalise** them to `Date` / `Description` / `Amount` — currently the
   column-mapping selectboxes.
3. **Categorize and commit** — `categorize_transactions()` then
   `SheetsClient.append_transactions()`.

Only step 1 is CSV-specific. To move to an aggregator (Plaid, GoCardless,
Teller, SimpleFIN, or a bank's own OFX/CAMT export):

1. **Write a fetcher** that returns a DataFrame with those three columns, plus
   `Account ID` where you can map it. Put it beside `scrape.py` as, say,
   `finance_app/bankfeed.py`, keeping credentials in `.env` alongside the
   others.
2. **Deduplicate on import.** A feed re-delivers the same transaction, which a
   CSV upload does not. Read `get_transactions()` first and drop rows already
   present — match on `(Date, Amount, Description)` rather than the provider's
   id, so a re-import after a manual edit does not duplicate. This is the one
   piece of real work in the swap.
3. **Replace the uploader** in `render()` with your fetcher call. The preview
   table, editable categories, rule learning, and the commit path all work
   unchanged, because they only ever see a DataFrame.
4. **Keep the preview.** Auto-committing a feed removes the moment where you
   correct a category — and every correction teaches a rule
   (`learn_from_edits`), so the categorizer stops needing the model over time.

Nothing in `logic/` or `data/` needs to change: the sheet schema, the
categorizer, and the writers are all independent of where rows came from.

---

## Architecture

```
finance_app/
  app.py            entry point, sidebar router, error boundary
  config.py         env vars; fails fast naming what is missing
  llm.py            the ONLY module that calls an LLM provider
  scrape.py         best-effort product lookup from a URL
  voice.py          optional voice input (record + local transcription)
  category_rules.json   user-editable merchant -> category rules
  data/
    models.py       frozen dataclasses; the single source of truth for the schema
    sheets.py       Google Sheets read/write, cached and quota-aware
  logic/            pure functions: no Streamlit, no Sheets, no network
    budget.py       dashboard and planning math
    paycheck.py     bi-weekly pay dates and allocation splitting
    affordability.py  purchase decisions and the verdict thresholds
    categorize.py   two-tier transaction categorization
    transactions.py filtering, sorting, and summarising the ledger
    wishlist.py     filtering, sorting, and stats
  pages_ui/         one module per page, plus shell.py for the error boundary
```

Three rules the code sticks to:

1. **`logic/` is pure.** It takes DataFrames and returns values, so all of it is
   testable without a browser or a network.
2. **The model never does arithmetic.** Every figure the Buy Advisor shows is
   computed in `affordability.py`; the model receives them already computed and
   writes prose. If the API fails, the verdict and numbers still render.
3. **Only `_`-prefixed tabs are writable.** A guard raises otherwise, so the
   formula-driven report can never be clobbered.

### Caching and quota

Google allows about 60 reads per minute per user, and Streamlit re-runs the
whole script on every interaction. So the authenticated client is cached as a
resource, every tab read is cached for 5 minutes, and writes are batched — a
10-category budget save costs 3 API calls, not 20. **🔄 Refresh data** in the
sidebar clears every cache after you edit the sheet by hand.

### Privacy

- Only aggregate computed figures reach the Gemini API. `llm_payload()` in
  `affordability.py` is the single chokepoint; account numbers,
  per-institution balances, and raw transactions never leave the machine.
- Merchant names are scrubbed of card numbers, account numbers, and reference
  IDs before categorization (`scrub_merchant`).
- Voice transcription runs locally via faster-whisper. No audio is uploaded.
