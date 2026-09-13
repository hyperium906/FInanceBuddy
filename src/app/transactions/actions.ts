"use server";

import { revalidateTag } from "next/cache";
import { learnFromEdits } from "@/lib/categorize";
import { TAB } from "@/lib/models";
import { loadData, SHEET_TAG } from "@/lib/server/data";
import { appendRecords, updateTransactions, type TransactionEdit } from "@/lib/server/sheets";

export interface RowEdit {
  id: string;
  category: string;
  notes: string;
  accountId: string;
}

/**
 * Write only the fields that differ from the sheet, then learn a rule from
 * every category corrected by hand so that merchant is right next import.
 */
export async function saveTransactions(edits: RowEdit[]): Promise<{ ok: boolean; message: string }> {
  try {
    const data = await loadData();
    const held = new Map(data.transactions.map((t) => [t.id, t]));
    const changes: Record<string, TransactionEdit> = {};
    const corrected: Array<{ description: string; category: string }> = [];
    const before: string[] = [];

    for (const edit of edits) {
      const row = held.get(edit.id);
      const fields: TransactionEdit = {};
      if (!row || edit.category !== row.category) fields.Category = edit.category;
      if (!row || edit.notes !== row.notes) fields.Notes = edit.notes;
      if (!row || edit.accountId !== row.accountId) fields["Account ID"] = edit.accountId;
      if (!Object.keys(fields).length) continue;
      changes[edit.id] = fields;
      if (row && fields.Category !== undefined) {
        corrected.push({ description: row.description, category: edit.category });
        before.push(row.category);
      }
    }

    const written = await updateTransactions(changes);
    revalidateTag(SHEET_TAG, { expire: 0 });

    const learned = learnFromEdits(corrected, before, data.rules);
    let note = "";
    if (learned.length && data.hasRulesTab) {
      await appendRecords(TAB.rules, learned.map((rule) => ({ Match: rule.match, Category: rule.category })));
      note = ` Learned ${learned.length} rule(s), so those merchants are categorized automatically next import.`;
    } else if (learned.length) {
      note = ` Add a ${TAB.rules} tab (headers Match, Category) and corrections like these will be remembered for future imports.`;
    }
    return { ok: true, message: `Saved ${written} transaction(s).${note}` };
  } catch (error) {
    return { ok: false, message: (error as Error).message };
  }
}
