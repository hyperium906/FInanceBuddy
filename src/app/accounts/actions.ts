"use server";

import { revalidateTag } from "next/cache";
import { parseMoney } from "@/lib/money";
import { SHEET_TAG } from "@/lib/server/data";
import { updateAccountBalances } from "@/lib/server/sheets";
import { fetchAccountSet } from "@/lib/server/simplefin";
import { stampNow } from "@/lib/server/stamp";
import { bankAccounts, errorMessages, type BankAccount } from "@/lib/simplefin";

export async function saveBalances(changes: Array<{ id: string; balance: string }>): Promise<{ ok: boolean; message: string }> {
  const rows = changes
    .map((c) => ({ id: c.id, balance: parseMoney(c.balance) }))
    .filter((c): c is { id: string; balance: number } => Boolean(c.id) && c.balance !== null);
  if (!rows.length) return { ok: false, message: "Nothing to save." };
  try {
    const written = await updateAccountBalances(rows, stampNow());
    revalidateTag(SHEET_TAG, { expire: 0 });
    return { ok: true, message: `Updated ${written} balance(s).` };
  } catch (error) {
    return { ok: false, message: (error as Error).message };
  }
}

/** Current balances from the linked banks. Read only — nothing is written until a balance is saved. */
export async function bankBalances(): Promise<{ ok: true; accounts: BankAccount[]; warnings: string[] } | { ok: false; error: string }> {
  try {
    const set = await fetchAccountSet({ balancesOnly: true });
    return { ok: true, accounts: bankAccounts(set, process.env.APP_TIMEZONE || "America/New_York"), warnings: errorMessages(set) };
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }
}
