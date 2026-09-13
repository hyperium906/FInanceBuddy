"use client";

import { useState, useTransition } from "react";
import { inputClass, primaryButton, secondaryButton } from "@/components/form";
import { Callout } from "@/components/ui";
import { format, type Day } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { guessBankAccount, type BankAccount } from "@/lib/simplefin";
import { bankBalances, saveBalances } from "./actions";

export interface AccountRow {
  id: string;
  name: string;
  type: string;
  institution: string;
  balance: number;
  lastUpdated: Day | null;
}

/**
 * Balances typed by hand, or taken from a linked bank. A bank balance is
 * offered beside the sheet's, never written over it: you choose which account
 * it belongs to and press Use, then Save.
 */
export function AccountsEditor({ rows, bankConfigured }: { rows: AccountRow[]; bankConfigured: boolean }) {
  const [balances, setBalances] = useState<Record<string, string>>(() => Object.fromEntries(rows.map((r) => [r.id, String(r.balance)])));
  const [bank, setBank] = useState<BankAccount[] | null>(null);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, startTransition] = useTransition();

  const changed = rows.filter((r) => balances[r.id] !== undefined && Number(balances[r.id]) !== r.balance);

  function save() {
    startTransition(async () => setNotice(await saveBalances(changed.map((r) => ({ id: r.id, balance: balances[r.id] })))));
  }

  function fetchBank() {
    startTransition(async () => {
      const result = await bankBalances();
      if (!result.ok) {
        setNotice({ ok: false, message: result.error });
        return;
      }
      setBank(result.accounts);
      setLinks(Object.fromEntries(rows.map((row) => [row.id, guessBankAccount(row, result.accounts)])));
      setNotice(result.warnings.length ? { ok: false, message: result.warnings.join(" ") } : null);
    });
  }

  return (
    <div className="space-y-3">
      {bankConfigured ? (
        <button onClick={fetchBank} disabled={pending} className={secondaryButton}>{bank ? "Refresh bank balances" : "Fetch balances from bank"}</button>
      ) : (
        <p className="text-sm text-[var(--muted)]">Link a bank on the Import page&apos;s &ldquo;Sync from bank&rdquo; tab to fill these in automatically.</p>
      )}

      <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-[var(--panel)] text-left text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Account</th>
              <th className="px-3 py-2 font-medium">Balance</th>
              {bank && <th className="px-3 py-2 font-medium">From bank</th>}
              <th className="px-3 py-2 font-medium">Last updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const linked = bank?.find((b) => b.id === links[r.id]);
              return (
                <tr key={r.id} className={`border-t border-[var(--border)] ${changed.includes(r) ? "bg-[var(--panel)]" : ""}`}>
                  <td className="px-3 py-1.5">
                    <strong>{r.name || r.id}</strong>
                    <div className="text-xs text-[var(--muted)]">{[r.institution, r.type, r.id].filter(Boolean).join(" · ")}</div>
                  </td>
                  <td className="px-3 py-1.5">
                    <input
                      type="number" step="0.01" value={balances[r.id] ?? ""}
                      onChange={(e) => setBalances((all) => ({ ...all, [r.id]: e.target.value }))}
                      className={`${inputClass} max-w-40 py-1`} aria-label={`Balance for ${r.name || r.id}`}
                    />
                    {changed.includes(r) && <div className="text-xs text-[var(--muted)]">was {fc(r.balance)}</div>}
                  </td>
                  {bank && (
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <select value={links[r.id] ?? ""} onChange={(e) => setLinks((all) => ({ ...all, [r.id]: e.target.value }))} className={`${inputClass} max-w-56 py-1`} aria-label={`Bank account for ${r.name || r.id}`}>
                          <option value="">— not linked —</option>
                          {bank.map((b) => <option key={b.id} value={b.id}>{[b.institution, b.name].filter(Boolean).join(" · ")}</option>)}
                        </select>
                        {linked && (
                          <button onClick={() => setBalances((all) => ({ ...all, [r.id]: String(linked.balance) }))} className={`${secondaryButton} whitespace-nowrap px-2 py-1`}>
                            Use {fc(linked.balance)}
                          </button>
                        )}
                      </div>
                      {linked?.balanceDate != null && <div className="text-xs text-[var(--muted)]">as of {format(linked.balanceDate, "DD Mon")}</div>}
                    </td>
                  )}
                  <td className="px-3 py-1.5 text-[var(--muted)]">{r.lastUpdated === null ? "—" : format(r.lastUpdated, "DD Mon YYYY")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {notice && <Callout tone={notice.ok ? "success" : "warn"}>{notice.message}</Callout>}
      <button onClick={save} disabled={!changed.length || pending} className={primaryButton}>
        {pending ? "Working…" : `Save ${changed.length} balance(s)`}
      </button>
    </div>
  );
}
