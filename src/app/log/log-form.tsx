"use client";

import { useActionState } from "react";
import { Field, inputClass, primaryButton, secondaryButton } from "@/components/form";
import { Callout, DataTable } from "@/components/ui";
import { logTransaction, type LogState } from "./actions";

export function LogForm({ accounts, defaultAccount, categories, today }: {
  accounts: string[]; defaultAccount: string; categories: string[]; today: string;
}) {
  const [state, action, pending] = useActionState<LogState, FormData>(logTransaction, { status: "idle" });
  const values = state.status === "error" || state.status === "confirm" ? state.values : null;

  return (
    <div className="space-y-4">
      {state.status === "ok" && <Callout tone="success">{state.message}</Callout>}

      {/* A fresh key after a successful write clears the form for the next entry. */}
      <form key={state.status === "ok" ? state.message : "entry"} action={action} className="space-y-4 rounded-lg border border-[var(--border)] p-4">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="Date">
            <input type="date" name="date" defaultValue={values?.date ?? today} className={inputClass} required />
          </Field>
          <Field label="Amount">
            <input type="number" name="amount" min="0" step="0.01" defaultValue={values?.amount ?? ""} className={inputClass} required />
          </Field>
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium">Direction</legend>
            <div className="flex gap-4 pt-2 text-sm">
              <label className="flex items-center gap-2">
                <input type="radio" name="direction" value="out" defaultChecked={(values?.direction ?? "out") === "out"} /> Money out
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" name="direction" value="in" defaultChecked={values?.direction === "in"} /> Money in
              </label>
            </div>
          </fieldset>
        </div>
        <Field label="What was it">
          <input name="description" placeholder="Tithe — Hope Church" defaultValue={values?.description ?? ""} className={inputClass} required />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Category">
            <select name="category" defaultValue={values?.category ?? ""} className={inputClass} required>
              <option value="" disabled>Choose…</option>
              {categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Account">
            <select name="account" defaultValue={values?.account ?? defaultAccount} className={inputClass}>
              {accounts.length ? accounts.map((a) => <option key={a}>{a}</option>) : <option value="">(no accounts)</option>}
            </select>
          </Field>
        </div>

        {state.status === "error" && <Callout tone="error">{state.message}</Callout>}

        {state.status === "confirm" ? (
          <div className="space-y-3">
            <Callout tone="warn"><strong>{state.message}</strong></Callout>
            <DataTable head={["Date", "Description", "Category", "Amount"]} numeric={[3]} rows={state.matches} />
            <div className="flex gap-3">
              <button name="force" value="1" className={primaryButton} disabled={pending}>Log it anyway</button>
              <button name="cancel" value="1" formNoValidate className={secondaryButton} disabled={pending}>Cancel</button>
            </div>
          </div>
        ) : (
          <button className={primaryButton} disabled={pending}>{pending ? "Logging…" : "Log it"}</button>
        )}
      </form>
    </div>
  );
}
