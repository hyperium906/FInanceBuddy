"use client";

import { useMemo, useState, useTransition } from "react";
import { inputClass, primaryButton, secondaryButton } from "@/components/form";
import { Callout, Caption } from "@/components/ui";
import { UNCATEGORIZED } from "@/lib/categorize";
import { format, type Day } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { saveTransactions, type RowEdit } from "./actions";

export interface EditableTransaction extends RowEdit {
  date: Day | null;
  description: string;
  amount: number;
}

/**
 * Category, notes and account are editable; date, amount and description stay
 * locked, because changing them would make the sheet disagree with the bank.
 */
export function TransactionsEditor({ rows, categories, accounts }: { rows: EditableTransaction[]; categories: string[]; accounts: string[] }) {
  const [edits, setEdits] = useState<Record<string, RowEdit>>(() => Object.fromEntries(rows.map((r) => [r.id, r])));
  const [search, setSearch] = useState("");
  const [uncategorizedOnly, setUncategorizedOnly] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, startTransition] = useTransition();

  const current = (row: EditableTransaction) => edits[row.id] ?? row;
  const changed = rows.filter((row) => {
    const e = current(row);
    return e.category !== row.category || e.notes !== row.notes || e.accountId !== row.accountId;
  });

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (uncategorizedOnly && row.category && row.category !== UNCATEGORIZED) return false;
      return !needle || `${row.description} ${row.category} ${row.notes}`.toLowerCase().includes(needle);
    });
  }, [rows, search, uncategorizedOnly]);

  const set = (id: string, patch: Partial<RowEdit>) =>
    setEdits((all) => ({ ...all, [id]: { ...(all[id] ?? rows.find((r) => r.id === id)!), ...patch } }));

  function save() {
    startTransition(async () => {
      const result = await saveTransactions(changed.map((row) => current(row)));
      setNotice(result);
    });
  }

  if (!rows.length) return <Callout>No transactions in this period.</Callout>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search description, category, notes…" className={`${inputClass} max-w-sm`} />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={uncategorizedOnly} onChange={(e) => setUncategorizedOnly(e.target.checked)} /> Uncategorized only
        </label>
        <Caption>{shown.length} of {rows.length} shown</Caption>
      </div>

      <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-[var(--panel)] text-left text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Date</th>
              <th className="px-3 py-2 font-medium">Description</th>
              <th className="px-3 py-2 text-right font-medium">Amount</th>
              <th className="px-3 py-2 font-medium">Category</th>
              <th className="px-3 py-2 font-medium">Notes</th>
              <th className="px-3 py-2 font-medium">Account</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const e = current(row);
              const dirty = changed.includes(row);
              const options = [...new Set([...categories, UNCATEGORIZED, row.category].filter(Boolean))];
              const accountOptions = [...new Set([...accounts, row.accountId].filter(Boolean))];
              return (
                <tr key={row.id} className={`border-t border-[var(--border)] ${dirty ? "bg-[var(--panel)]" : ""}`}>
                  <td className="whitespace-nowrap px-3 py-1.5">{row.date === null ? "—" : format(row.date, "Dow DD Mon")}</td>
                  <td className="px-3 py-1.5">{row.description}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fc(row.amount)}</td>
                  <td className="px-3 py-1.5">
                    <select value={e.category} onChange={(ev) => set(row.id, { category: ev.target.value })} className={`${inputClass} py-1`} aria-label={`Category for ${row.description}`}>
                      {!e.category && <option value="">—</option>}
                      {options.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-1.5">
                    <input value={e.notes} onChange={(ev) => set(row.id, { notes: ev.target.value })} className={`${inputClass} py-1`} aria-label={`Notes for ${row.description}`} />
                  </td>
                  <td className="px-3 py-1.5">
                    <select value={e.accountId} onChange={(ev) => set(row.id, { accountId: ev.target.value })} className={`${inputClass} py-1`} aria-label={`Account for ${row.description}`}>
                      {!e.accountId && <option value="">—</option>}
                      {accountOptions.map((a) => <option key={a}>{a}</option>)}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {notice && <Callout tone={notice.ok ? "success" : "error"}>{notice.message}</Callout>}
      <div className="sticky bottom-0 flex gap-3 bg-[var(--bg)] py-2">
        <button onClick={save} disabled={!changed.length || pending} className={primaryButton}>
          {pending ? "Saving…" : `Save ${changed.length} change(s)`}
        </button>
        <button onClick={() => setEdits(Object.fromEntries(rows.map((r) => [r.id, r])))} disabled={!changed.length || pending} className={secondaryButton}>
          Discard
        </button>
      </div>
    </div>
  );
}
