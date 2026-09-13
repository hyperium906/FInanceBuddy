/**
 * Reading a bank's CSV export, and keeping a re-upload from duplicating it.
 *
 * *The trailing comma.* Chase ends every data row with one, giving each row a
 * field more than the header names. Read naively, every value shifts one
 * column left — dates fill with merchant names — and nothing raises. Rows here
 * are cut to the header's width and an empty unnamed column is dropped.
 *
 * *The overlap.* Uploading once a payday means consecutive exports cover the
 * same days twice. Matching is on date, amount and description, never the
 * bank's id, so a row whose category was fixed by hand is still recognised.
 */

import Papa from "papaparse";
import { parseDay, type Day } from "./dates";
import { parseMoney, round2 } from "./money";

export const REQUIRED = ["Date", "Description", "Amount"] as const;

/** Notes on a row typed in on the Log page rather than imported. */
export const LOGGED_BY_HAND = "Logged by hand";

/** A card gift on Saturday posts Monday; a check can take most of a week. */
export const LOGGED_WINDOW_DAYS = 5;

const HINTS: Record<(typeof REQUIRED)[number], string[]> = {
  Date: ["posting date", "post date", "transaction date", "date"],
  Description: ["description", "payee", "merchant", "name", "memo"],
  Amount: ["amount", "debit", "credit", "value"],
};

export interface RawCsv {
  columns: string[];
  rows: string[][];
}

export type Mapping = Record<(typeof REQUIRED)[number], string>;

/** A transaction as `_Transactions` holds it, before it has an id. */
export interface LedgerRow {
  date: Day | null;
  description: string;
  amount: number;
  category: string;
  accountId: string;
  notes: string;
}

export function readBankCsv(text: string): RawCsv {
  const parsed = Papa.parse<string[]>(text, { skipEmptyLines: true });
  const [head = [], ...body] = parsed.data;

  const seen = new Map<string, number>();
  const names = head.map((raw, index) => {
    let name = String(raw ?? "").trim() || `Unnamed: ${index}`;
    const count = seen.get(name) ?? 0;
    seen.set(name, count + 1);
    if (count) name = `${name}.${count}`;
    return name;
  });
  const width = names.length;
  const rows = body.map((row) => Array.from({ length: width }, (_, i) => String(row[i] ?? "")));

  const keep = names
    .map((name, index) => ({ name, index }))
    .filter(({ name, index }) => !(name.startsWith("Unnamed:") && !rows.some((row) => row[index].trim())));

  return {
    columns: keep.map((k) => k.name),
    rows: rows.map((row) => keep.map((k) => row[k.index])),
  };
}

/** Best guess at which column holds the date, description and amount. */
export function guessColumns(raw: RawCsv): Mapping {
  const out: Partial<Mapping> = {};
  for (const wanted of REQUIRED) {
    for (const hint of HINTS[wanted]) {
      const match = raw.columns.find((c) => c.trim().toLowerCase().includes(hint) && !Object.values(out).includes(c));
      if (match !== undefined) {
        out[wanted] = match;
        break;
      }
    }
    out[wanted] ??= raw.columns[0] ?? "";
  }
  return out as Mapping;
}

/**
 * Reshape an export into ledger rows. An unreadable date or amount is kept as
 * blank/zero rather than dropped, so a malformed line is visible in the preview
 * instead of vanishing between the file and the sheet.
 */
export function normalise(raw: RawCsv, mapping: Mapping, accountId = ""): LedgerRow[] {
  const at = (name: string) => raw.columns.indexOf(name);
  const [d, desc, amt] = [at(mapping.Date), at(mapping.Description), at(mapping.Amount)];
  return raw.rows.map((row) => ({
    date: parseDay(row[d]),
    description: String(row[desc] ?? "").trim(),
    amount: parseMoney(row[amt]) ?? 0,
    category: "",
    accountId,
    notes: "",
  }));
}

type Keyed = Pick<LedgerRow, "date" | "amount" | "description">;

/** Identity of a transaction: date, amount and description — an identity that survives editing. */
function key(row: Keyed): string {
  return `${row.date ?? ""}|${round2(row.amount || 0)}|${String(row.description ?? "").trim().toLowerCase()}`;
}

/** Rows of `rows` for the same signed amount within the logging window of `when`. */
export function near<T extends Keyed>(rows: T[], when: Day, amount: number): boolean[] {
  return rows.map(
    (row) => row.date !== null && round2(row.amount || 0) === round2(amount) && Math.abs(row.date - when) <= LOGGED_WINDOW_DAYS,
  );
}

export function loggedByHand<T extends { notes: string }>(rows: T[]): T[] {
  return rows.filter((row) => String(row.notes ?? "").trim().toLowerCase().startsWith(LOGGED_BY_HAND.toLowerCase()));
}

export interface Deduplicated<T> {
  fresh: T[];
  alreadyHeld: number;
  repeatedInFile: number;
  loggedByHand: number;
}

export const dropped = (result: Deduplicated<unknown>) =>
  result.alreadyHeld + result.repeatedInFile + result.loggedByHand;

/**
 * Drop rows the sheet already holds, rows repeated within the file, and bank
 * lines for something already logged by hand — each counted apart, because
 * they mean different things.
 */
export function deduplicate<T extends Keyed>(incoming: T[], existing: Array<Keyed & { notes: string }>): Deduplicated<T> {
  const held = new Set(existing.map(key));
  const seenInFile = new Set<string>();
  const heldFlags: boolean[] = [];
  const withinFlags: boolean[] = [];
  for (const row of incoming) {
    const k = key(row);
    const before = held.has(k);
    heldFlags.push(before);
    withinFlags.push(seenInFile.has(k) && !before);
    seenInFile.add(k);
  }

  // The descriptions never agree — "Tithe" against "HOPE CHURCH 478-9537898" —
  // so same amount inside the window is the match, and each logged row claims
  // at most one line: a second gift of the same amount is a second gift.
  const claimed = incoming.map(() => false);
  for (const logged of loggedByHand(existing)) {
    if (logged.date === null) continue;
    const matches = near(incoming, logged.date, logged.amount);
    const index = matches.findIndex((m, i) => m && !heldFlags[i] && !withinFlags[i] && !claimed[i]);
    if (index >= 0) claimed[index] = true;
  }

  return {
    fresh: incoming.filter((_, i) => !heldFlags[i] && !withinFlags[i] && !claimed[i]),
    alreadyHeld: heldFlags.filter(Boolean).length,
    repeatedInFile: withinFlags.filter(Boolean).length,
    loggedByHand: claimed.filter(Boolean).length,
  };
}

/** First and last date among `rows`. */
export function span(rows: Array<{ date: Day | null }>): [Day | null, Day | null] {
  const dates = rows.map((r) => r.date).filter((d): d is Day => d !== null);
  if (!dates.length) return [null, null];
  return [Math.min(...dates), Math.max(...dates)];
}
