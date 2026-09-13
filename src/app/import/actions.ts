"use server";

import { revalidateTag } from "next/cache";
import { learnFromEdits, UNCATEGORIZED } from "@/lib/categorize";
import { iso, type Day } from "@/lib/dates";
import { TAB } from "@/lib/models";
import { categorizeRows } from "@/lib/server/categorizer";
import { loadData, SHEET_TAG } from "@/lib/server/data";
import { appendRecords } from "@/lib/server/sheets";
import { fetchAccountSet } from "@/lib/server/simplefin";
import { bankAccounts, errorMessages, flagNearDuplicates, syncIdIn, toLedgerRows, type BankAccount } from "@/lib/simplefin";
import { deduplicate, normalise, readBankCsv, span, type LedgerRow, type Mapping } from "@/lib/statements";

const timeZone = () => process.env.APP_TIMEZONE || "America/New_York";

export type StageResult =
  | { ok: false; error: string }
  | {
      ok: true;
      source: "csv" | "bank";
      incoming: number;
      fresh: LedgerRow[];
      /** Per fresh row: the sheet may already hold it under another description. */
      maybe: boolean[];
      alreadyHeld: number;
      repeatedInFile: number;
      loggedByHand: number;
      first: Day | null;
      last: Day | null;
      moneyOut: number;
      warnings: string[];
    };

function staged(source: "csv" | "bank", incoming: LedgerRow[], fresh: LedgerRow[], counts: { alreadyHeld: number; repeatedInFile: number; loggedByHand: number }, maybe: boolean[], warnings: string[] = []): StageResult {
  const [first, last] = span(incoming);
  return {
    ok: true,
    source,
    incoming: incoming.length,
    fresh,
    maybe,
    ...counts,
    first,
    last,
    moneyOut: fresh.filter((r) => r.amount < 0).reduce((s, r) => s + r.amount, 0),
    warnings,
  };
}

/** Normalise an upload and drop what the sheet already holds. Nothing is written. */
export async function stageImport(input: { csv: string; mapping: Mapping; accountId: string }): Promise<StageResult> {
  try {
    const raw = readBankCsv(String(input.csv ?? ""));
    if (!raw.rows.length) return { ok: false, error: "That file has no rows in it." };
    const chosen = Object.values(input.mapping ?? {});
    if (new Set(chosen).size < 3 || chosen.some((c) => !raw.columns.includes(c))) {
      return { ok: false, error: "Date, description and amount need to be three different columns from the file." };
    }
    const incoming = normalise(raw, input.mapping, String(input.accountId ?? ""));
    const { transactions } = await loadData();
    const result = deduplicate(incoming, transactions);
    return staged("csv", incoming, result.fresh, result, result.fresh.map(() => false));
  } catch (error) {
    return { ok: false, error: `Could not read that file: ${(error as Error).message}` };
  }
}

export async function listBankAccounts(): Promise<{ ok: true; accounts: BankAccount[]; warnings: string[] } | { ok: false; error: string }> {
  try {
    const set = await fetchAccountSet({ balancesOnly: true });
    return { ok: true, accounts: bankAccounts(set, timeZone()), warnings: errorMessages(set) };
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }
}

/**
 * Fetch one linked account's postings since `startDay` and stage them exactly
 * like an upload: rows synced before are recognised by id, rows imported from
 * a CSV by the usual identity, and rows that might be the same purchase under
 * the bank feed's different wording are flagged for a decision.
 */
export async function syncFromBank(input: { bankAccountId: string; accountId: string; startDay: Day }): Promise<StageResult> {
  try {
    const startDay = Number(input.startDay);
    const set = await fetchAccountSet({ startDate: (startDay - 1) * 86_400, accounts: [String(input.bankAccountId)] });
    const warnings = errorMessages(set);
    const account = set.accounts.find((a) => a.id === input.bankAccountId);
    if (!account) return { ok: false, error: warnings.join(" ") || "SimpleFIN returned nothing for that account." };

    const incoming = toLedgerRows(account, String(input.accountId ?? ""), timeZone()).filter((r) => r.date === null || r.date >= startDay);
    const { transactions } = await loadData();
    const synced = new Set(transactions.map((t) => syncIdIn(t.notes)).filter(Boolean));
    const unseen = incoming.filter((r) => !synced.has(syncIdIn(r.notes)));
    const result = deduplicate(unseen, transactions);
    return staged(
      "bank",
      incoming,
      result.fresh,
      { ...result, alreadyHeld: result.alreadyHeld + incoming.length - unseen.length },
      flagNearDuplicates(result.fresh, transactions),
      warnings,
    );
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }
}

export type CategorizeResult =
  | { ok: false; error: string }
  | { ok: true; rows: LedgerRow[]; ruleHits: number; llmHits: number; unresolved: number; failures: string[] };

/** Rules first, the model only for the rest. A failed batch costs rows a category, not the import. */
export async function categorizeImport(rows: LedgerRow[]): Promise<CategorizeResult> {
  try {
    const { rules } = await loadData();
    const cleared = rows.map((row) => ({ ...row, category: "" }));
    return { ok: true, ...(await categorizeRows(cleared, rules)) };
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }
}

export type CommitResult = { ok: boolean; message: string };

/**
 * Write the reviewed rows, then remember every correction as a rule.
 *
 * Transactions go first: they are what the import is for. A rule that fails to
 * save costs a future model call, and the message says so rather than failing
 * an import that already landed.
 */
export async function commitImport(input: { rows: LedgerRow[]; baseline: string[] }): Promise<CommitResult> {
  const rows = input.rows ?? [];
  if (!rows.length) return { ok: false, message: "Nothing to commit." };

  let written: number;
  let data;
  try {
    data = await loadData();
    written = await appendRecords(
      TAB.transactions,
      rows.map((row) => ({
        "Transaction ID": "",
        Date: row.date === null ? "" : iso(row.date),
        "Account ID": row.accountId,
        Description: row.description,
        Category: row.category || UNCATEGORIZED,
        Amount: row.amount,
        Notes: row.notes,
      })),
      "Transaction ID",
    );
  } catch (error) {
    return { ok: false, message: (error as Error).message };
  }
  revalidateTag(SHEET_TAG, { expire: 0 });

  const learned = learnFromEdits(rows, input.baseline ?? [], data.rules);
  let note = "";
  if (learned.length && data.hasRulesTab) {
    try {
      await appendRecords(TAB.rules, learned.map((rule) => ({ Match: rule.match, Category: rule.category })));
      note = ` Learned ${learned.length} categorization rule(s).`;
    } catch (error) {
      note = ` The transactions are in, but saving ${learned.length} learned rule(s) failed: ${(error as Error).message}`;
    }
  } else if (learned.length) {
    note = ` ${learned.length} correction(s) were not remembered: add a tab named ${TAB.rules} with the headers Match and Category to keep them.`;
  }
  return { ok: true, message: `Wrote ${written} transaction(s).${note}` };
}
