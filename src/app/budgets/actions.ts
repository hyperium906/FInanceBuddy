"use server";

import { revalidateTag } from "next/cache";
import { parseMoney } from "@/lib/money";
import { SHEET_TAG } from "@/lib/server/data";
import { upsertBudgets } from "@/lib/server/sheets";

export async function saveBudgets(month: string, entries: Array<{ category: string; amount: string }>): Promise<{ ok: boolean; message: string }> {
  if (!/^\d{4}-\d{2}$/.test(month)) return { ok: false, message: `'${month}' is not a month (YYYY-MM).` };
  const rows = entries
    .filter((entry) => String(entry.amount ?? "").trim() !== "")
    .map((entry) => ({ category: entry.category, amount: Math.max(parseMoney(entry.amount) ?? 0, 0) }));
  if (!rows.length) return { ok: false, message: "Nothing to save — every amount is blank." };
  try {
    const written = await upsertBudgets(month, rows);
    revalidateTag(SHEET_TAG, { expire: 0 });
    return { ok: true, message: `Saved ${written} budget(s) for ${month}.` };
  } catch (error) {
    return { ok: false, message: (error as Error).message };
  }
}
