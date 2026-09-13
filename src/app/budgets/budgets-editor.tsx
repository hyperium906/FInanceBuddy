"use client";

import { useState, useTransition } from "react";
import { inputClass, primaryButton } from "@/components/form";
import { Callout, Caption } from "@/components/ui";
import { formatCurrency as fc, parseMoney } from "@/lib/money";
import { perPeriod } from "@/lib/periods";
import { saveBudgets } from "./actions";

export function BudgetsEditor({ month, entries, cadence, copiedFrom }: {
  month: string; entries: Array<{ category: string; amount: number | null; spent: number }>; cadence: string; copiedFrom: string | null;
}) {
  const [amounts, setAmounts] = useState<Record<string, string>>(() =>
    Object.fromEntries(entries.map((e) => [e.category, e.amount === null ? "" : String(e.amount)])),
  );
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, startTransition] = useTransition();

  const total = entries.reduce((s, e) => s + (parseMoney(amounts[e.category]) ?? 0), 0);

  function save() {
    startTransition(async () => {
      setNotice(await saveBudgets(month, entries.map((e) => ({ category: e.category, amount: amounts[e.category] ?? "" }))));
    });
  }

  return (
    <div className="space-y-3">
      {copiedFrom && (
        <Callout tone="warn">No budgets are set for {month} yet. These are {copiedFrom}&apos;s figures — the app already falls back to them — and saving writes them to {month}.</Callout>
      )}
      <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-[var(--panel)] text-left text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Category</th>
              <th className="px-3 py-2 font-medium">Monthly budget</th>
              <th className="px-3 py-2 text-right font-medium">Per check</th>
              <th className="px-3 py-2 text-right font-medium">Spent this check</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => {
              const monthly = parseMoney(amounts[e.category]);
              return (
                <tr key={e.category} className="border-t border-[var(--border)]">
                  <td className="px-3 py-1.5">{e.category}</td>
                  <td className="px-3 py-1.5">
                    <input
                      type="number" min="0" step="10" value={amounts[e.category] ?? ""} placeholder="not budgeted"
                      onChange={(ev) => setAmounts((all) => ({ ...all, [e.category]: ev.target.value }))}
                      className={`${inputClass} max-w-40 py-1`} aria-label={`Monthly budget for ${e.category}`}
                    />
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{monthly ? fc(perPeriod(monthly, cadence)) : "—"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{e.spent ? fc(e.spent) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Caption>
        <strong>{fc(total)}</strong> a month, {fc(perPeriod(total, cadence))} a check. Per-check shares go through 26 checks a year, not two a month.
        A blank amount leaves that category as it is.
      </Caption>
      {notice && <Callout tone={notice.ok ? "success" : "error"}>{notice.message}</Callout>}
      <button onClick={save} disabled={pending} className={primaryButton}>{pending ? "Saving…" : `Save budgets for ${month}`}</button>
    </div>
  );
}
