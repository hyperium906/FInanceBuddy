"use server";

import { revalidateTag } from "next/cache";
import { iso, today } from "@/lib/dates";
import { nextDueFromDay, TAB } from "@/lib/models";
import { parseMoney } from "@/lib/money";
import { normaliseFrequency } from "@/lib/recurring";
import { SHEET_TAG } from "@/lib/server/data";
import { appendRecords, newId, updateRecord } from "@/lib/server/sheets";

export type BillState = { status: "idle" } | { status: "ok" | "error"; message: string };

/**
 * Columns a `_Recurring` tab may not have. The due day in particular lives in
 * one of two shapes — a `Next Due` date or a `due_day` number — and whichever
 * the sheet uses is the one written.
 */
const OPTIONAL = ["Category", "Frequency", "Active", "Merchant", "Estimated", "Confirmed", "Tax Rate", "Variable", "Next Due", "due_day"];

function read(form: FormData): { name: string; error?: string; fields: Record<string, unknown> } {
  const name = String(form.get("name") ?? "").trim();
  const amount = Math.abs(parseMoney(form.get("amount")) ?? 0);
  const dueDay = parseMoney(form.get("dueDay"));
  const tax = parseMoney(form.get("taxRate")) ?? 0;

  let error: string | undefined;
  if (!name) error = "It needs a name.";
  else if (!amount) error = "It needs an amount above zero.";
  else if (dueDay === null || dueDay < 1 || dueDay > 31) error = "The day it bills needs to be 1–31.";

  const next = dueDay === null ? null : nextDueFromDay(dueDay, today());
  return {
    name,
    error,
    fields: {
      Name: name,
      Category: String(form.get("category") ?? ""),
      Amount: amount,
      Frequency: normaliseFrequency(form.get("frequency")),
      Active: form.get("active") === "on",
      Merchant: String(form.get("merchant") ?? "").trim().toLowerCase(),
      Estimated: form.get("estimated") === "on",
      Confirmed: form.get("confirmed") === "on",
      "Tax Rate": tax,
      Variable: form.get("variable") === "on",
      "Next Due": next === null ? "" : iso(next),
      due_day: dueDay === null ? "" : Math.trunc(dueDay),
    },
  };
}

export async function updateBill(_prev: BillState, form: FormData): Promise<BillState> {
  const id = String(form.get("id") ?? "").trim();
  const { name, error, fields } = read(form);
  if (!id) return { status: "error", message: "This row has no Recurring ID, so it can only be edited in the sheet." };
  if (error) return { status: "error", message: error };
  try {
    await updateRecord(TAB.recurring, "Recurring ID", id, fields, `No bill with Recurring ID '${id}'.`, OPTIONAL);
  } catch (e) {
    return { status: "error", message: (e as Error).message };
  }
  revalidateTag(SHEET_TAG, { expire: 0 });
  return { status: "ok", message: `Saved ${name}.` };
}

export async function addBill(_prev: BillState, form: FormData): Promise<BillState> {
  const { name, error, fields } = read(form);
  if (error) return { status: "error", message: error };
  try {
    await appendRecords(TAB.recurring, [{ "Recurring ID": newId("r"), ...fields }]);
  } catch (e) {
    return { status: "error", message: (e as Error).message };
  }
  revalidateTag(SHEET_TAG, { expire: 0 });
  return { status: "ok", message: `Added ${name}.` };
}
