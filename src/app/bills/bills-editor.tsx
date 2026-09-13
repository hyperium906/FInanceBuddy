"use client";

import { useActionState } from "react";
import { Field, inputClass, primaryButton } from "@/components/form";
import { Callout, StatusChip } from "@/components/ui";
import { format, type Day } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import type { Status } from "@/lib/spending";
import { addBill, updateBill, type BillState } from "./actions";

export interface BillRow {
  id: string;
  name: string;
  category: string;
  amount: number;
  frequency: string;
  dueDay: number | null;
  active: boolean;
  merchant: string;
  estimated: boolean;
  confirmed: boolean;
  taxRate: number;
  variable: boolean;
  billed: number;
  next: Day | null;
  verdict: "confirmed" | "wrong-day" | "estimated" | "unseen" | null;
  note: string;
}

export const FREQUENCY_LABELS: Record<string, string> = {
  weekly: "Weekly", biweekly: "Every 2 weeks", semimonthly: "Twice a month", monthly: "Monthly",
  quarterly: "Quarterly", semiannual: "Every 6 months", annual: "Annually",
};

const VERDICT: Record<NonNullable<BillRow["verdict"]>, [Status, string]> = {
  confirmed: ["green", "seen in the statement"],
  "wrong-day": ["amber", "bills on a different day"],
  estimated: ["none", "estimate"],
  unseen: ["red", "never seen"],
};

function BillFields({ row, categories }: { row?: BillRow; categories: string[] }) {
  const options = [...new Set([...categories, ...(row?.category ? [row.category] : [])])];
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Name"><input name="name" defaultValue={row?.name ?? ""} className={inputClass} required /></Field>
        <Field label="Amount" hint="Before tax.">
          <input name="amount" type="number" min="0" step="0.01" defaultValue={row?.amount ?? ""} className={inputClass} required />
        </Field>
        <Field label="Bills on day" hint="Day of the month, 1–31.">
          <input name="dueDay" type="number" min="1" max="31" defaultValue={row?.dueDay ?? ""} className={inputClass} required />
        </Field>
        <Field label="Category">
          <select name="category" defaultValue={row?.category ?? "Subscriptions"} className={inputClass}>
            {options.map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="How often">
          <select name="frequency" defaultValue={row?.frequency ?? "monthly"} className={inputClass}>
            {Object.entries(FREQUENCY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        <Field label="Sales tax %" hint="Only if tax is added at the till.">
          <input name="taxRate" type="number" min="0" step="0.01" defaultValue={row?.taxRate || ""} className={inputClass} />
        </Field>
      </div>
      <Field label="Shows on the statement as" hint="A phrase from the bank description, e.g. apple.com — how the charge is matched back to this bill.">
        <input name="merchant" defaultValue={row?.merchant ?? ""} className={inputClass} />
      </Field>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" name="active" defaultChecked={row?.active ?? true} /> Active</label>
        <label className="flex items-center gap-2"><input type="checkbox" name="confirmed" defaultChecked={row?.confirmed ?? false} /> I know this recurs</label>
        <label className="flex items-center gap-2"><input type="checkbox" name="estimated" defaultChecked={row?.estimated ?? false} /> Amount is an estimate</label>
        <label className="flex items-center gap-2"><input type="checkbox" name="variable" defaultChecked={row?.variable ?? false} /> Amount varies (metered)</label>
      </div>
    </div>
  );
}

function StateNote({ state }: { state: BillState }) {
  return state.status === "idle" ? null : <Callout tone={state.status === "ok" ? "success" : "error"}>{state.message}</Callout>;
}

function BillItem({ row, categories, today }: { row: BillRow; categories: string[]; today: Day }) {
  const [state, action, pending] = useActionState<BillState, FormData>(updateBill, { status: "idle" });
  const verdict = row.active && row.verdict ? VERDICT[row.verdict] : null;

  return (
    <details className={`rounded-lg border border-[var(--border)] ${row.active ? "" : "opacity-60"}`}>
      <summary className="grid cursor-pointer grid-cols-[1fr_auto] gap-x-4 gap-y-1 p-3 md:grid-cols-[2fr_1fr_1fr]">
        <div>
          <strong>{row.name}</strong>
          {row.merchant && <span className="ml-2 text-xs text-[var(--muted)]">bills as {row.merchant}</span>}
          {!row.active && <span className="ml-2 text-xs text-[var(--muted)]">switched off</span>}
          <div className="text-xs text-[var(--muted)]">{row.category || "—"} · {FREQUENCY_LABELS[row.frequency] ?? row.frequency}</div>
        </div>
        <div className="text-right tabular-nums md:text-left">
          {fc(row.billed)}
          {row.taxRate > 0 && <div className="text-xs text-[var(--muted)]">{fc(row.amount)} + {row.taxRate}% tax</div>}
        </div>
        <div className="col-span-2 text-sm md:col-span-1">
          {row.next !== null ? (
            <>
              {format(row.next, "Dow DD Mon")}{" "}
              <span className="text-[var(--muted)]">({row.next - today === 0 ? "today" : `in ${row.next - today} days`})</span>
            </>
          ) : row.active ? "no due day set" : "—"}
        </div>
        {verdict && (
          <div className="col-span-2 text-sm md:col-span-3">
            <StatusChip status={verdict[0]}>{verdict[1]}</StatusChip>
            <span className="ml-2 text-[var(--muted)]">{row.note}</span>
          </div>
        )}
      </summary>
      <form action={action} className="space-y-4 border-t border-[var(--border)] p-3">
        <input type="hidden" name="id" value={row.id} />
        {!row.id && <Callout tone="warn">This row has no Recurring ID in the sheet, so it can only be edited there.</Callout>}
        <BillFields row={row} categories={categories} />
        <StateNote state={state} />
        <button className={primaryButton} disabled={pending || !row.id}>{pending ? "Saving…" : "Save changes"}</button>
      </form>
    </details>
  );
}

function AddBill({ categories }: { categories: string[] }) {
  const [state, action, pending] = useActionState<BillState, FormData>(addBill, { status: "idle" });
  return (
    <details className="rounded-lg border border-dashed border-[var(--border)] p-3">
      <summary className="cursor-pointer font-medium">+ Add a bill or subscription</summary>
      <form key={state.status === "ok" ? state.message : "new"} action={action} className="mt-4 space-y-4">
        <BillFields categories={categories} />
        <StateNote state={state} />
        <button className={primaryButton} disabled={pending}>{pending ? "Adding…" : "Add it"}</button>
      </form>
    </details>
  );
}

export function BillsEditor({ rows, categories, today }: { rows: BillRow[]; categories: string[]; today: Day }) {
  return (
    <div className="space-y-2">
      {rows.map((row) => <BillItem key={row.id || row.name} row={row} categories={categories} today={today} />)}
      <AddBill categories={categories} />
    </div>
  );
}
