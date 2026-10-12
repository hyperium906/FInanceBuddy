@AGENTS.md

# FinanceBuddy

A personal budgeting app for one person, paid biweekly on Fridays. It reads and writes a hand-maintained Google Sheet. **The sheet is the product**: the app feeds the workbook and never replaces it.

Stack: Next.js 16 (App Router, Turbopack) + TypeScript + Tailwind 4, deployed to Vercel. Tests use Vitest. Python and Streamlit were removed on 2026-09-13; the last Python commit is `5476808`.

## Commands

- `npm run dev`: dev server at http://localhost:3000 (reads `.env`)
- `npm test`: Vitest (207 tests, `tests/`)
- `npm run typecheck`: `tsc --noEmit`. If it reports missing `src/app/*/page.js`, delete the stale `.next/types` folder.
- `npx next build`: production build. Every data page must show as ƒ (dynamic), not ○.
- `npm run simplefin:claim -- <token>`: turns a SimpleFIN Bridge setup token into `SIMPLEFIN_ACCESS_URL` in `.env`

## Layout

- `src/lib/`: pure domain logic, ported from Python and fully tested. `dates` (a `Day` is an integer day number), `money`, `models` (sheet schema, column aliases, foreign layouts such as `due_day`), `periods` (pay periods: 26 checks a year, never "two a month"), `recurring`, `billing` (matches bills to statement charges), `allocations`, `commitments` (which check carries which bill; running balance), `spending`, `savings`, `wishlist`, `statements` (CSV import and dedup), `categorize`, `products`, `simplefin`.
- `src/lib/server/`: `config` (env), `sheets` (Sheets REST client plus the write guard), `data` (`loadData()`, cached 5 minutes under the tag `sheet`), `llm` + `categorizer` (Gemini), `simplefin` (Bridge client), `stamp`.
- `src/components/`: `ui` (Card, Stat, Meter, StatusChip, Callout, PageTitle), `charts` (CompositionBar), `form`, `guard` (`withSheet()`: every page renders inside it; it calls `connection()` and turns errors into readable messages).
- `src/app/`: `page.tsx` (the dashboard), `layout.tsx` (sidebar; the `PAGES` list is where new pages get added), `actions.ts` (`refreshData`), `login/`. `src/proxy.ts` is the `APP_PASSWORD` gate.

## Rules that matter

- Writes go only to `_`-prefixed tabs, plus `Wishlist & Purchases` (append above its Total row). Never write `Budget Sheet`.
- Writes position cells by the tab's **live** header, read fresh, never by schema order.
- After any write in a server action: `revalidateTag(SHEET_TAG, { expire: 0 })`.
- Category names come from the sheet's vocabulary (`CATEGORIES` in `src/lib/categorize.ts`).
- Never infer a subscription from a single charge. Ask.
- `_Allocations` amounts are **monthly**. Fixed rules come out whole from the first check of the month; percentage rules apply to every check. The second check of each month is the rent check and needs a carry from the first.
- The user's "Apple subscription" is the `_Recurring` row **Software / Music** (merchant `apple.com`).
- Secrets (the Google key, the Gemini key, `SIMPLEFIN_ACCESS_URL`) live only in env vars. Uploading them to Vercel, or creating new tabs in the workbook, needs the user's explicit OK.

## Status (end of 2026-09-13), picking up tomorrow

We are **rebuilding the UI one page at a time**. The user cleared every page to rebuild each one with them.

- **Done:** Dashboard (`/`), with four read-only sections: This check, Coming up, Spending, Savings & balances. It is verified against the real sheet. **Not committed yet.**
- **Previous version of every page** is in commit `8596abe`. Recover one with `git show 8596abe:src/app/<page>/page.tsx`. Those pages were Payday, Log, Bills (editable `_Recurring`), Transactions (editable category/notes/account), Budgets, Accounts (balances plus bank balances), Savings (goals), Wishlist (add from a product link), and Import (CSV plus SimpleFIN sync).
- The `src/lib/server/sheets.ts` write functions for all of those still exist: `appendRecords`, `updateRecord`, `updateTransactions`, `upsertBudgets`, `updateAccountBalances`, `appendPlannerRow`.

**Next session:**
1. Ask what to change on the dashboard, then which page to build next.
2. For each new page, add it to `PAGES` in `layout.tsx`.

**Open decisions for the user:**
- Commit the dashboard.
- Deploy to Vercel, which needs env vars including `APP_PASSWORD`.
- Claim a SimpleFIN token; no bank is linked yet.
- Create a `_Rules` tab (Match, Category) so category corrections are remembered.
- Delete the leftover `.streamlit/secrets.toml`.

**Never tested with real writes:** no write path has touched the sheet yet. Gemini categorization and SimpleFIN are also untested live.
