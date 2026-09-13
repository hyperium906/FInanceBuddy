"use server";

import { revalidateTag } from "next/cache";
import { format, iso, parseDay } from "@/lib/dates";
import { TAB } from "@/lib/models";
import { formatCurrency as fc, parseMoney } from "@/lib/money";
import { loadData, SHEET_TAG } from "@/lib/server/data";
import { appendRecords } from "@/lib/server/sheets";
import { LOGGED_BY_HAND, LOGGED_WINDOW_DAYS, near } from "@/lib/statements";

export interface LogValues {
  date: string;
  amount: string;
  direction: string;
  description: string;
  category: string;
  account: string;
}

export type LogState =
  | { status: "idle" }
  | { status: "ok"; message: string }
  | { status: "error"; message: string; values: LogValues }
  | { status: "confirm"; message: string; values: LogValues; matches: string[][] };

/**
 * Write a hand-logged row — unless the sheet may already hold it, in which
 * case pause and show the candidates first. `force` is the "log it anyway".
 */
export async function logTransaction(_prev: LogState, form: FormData): Promise<LogState> {
  if (form.get("cancel")) return { status: "idle" };

  const values: LogValues = {
    date: String(form.get("date") ?? ""),
    amount: String(form.get("amount") ?? ""),
    direction: String(form.get("direction") ?? "out"),
    description: String(form.get("description") ?? "").trim(),
    category: String(form.get("category") ?? ""),
    account: String(form.get("account") ?? ""),
  };

  const when = parseDay(values.date);
  const amount = parseMoney(values.amount) ?? 0;
  const missing = [
    [amount > 0, "an amount"],
    [values.description, "what it was"],
    [values.category, "a category"],
    [when !== null, "a date"],
  ].filter(([ok]) => !ok).map(([, name]) => name);
  if (missing.length) return { status: "error", message: `Still needs ${missing.join(", ")}.`, values };

  const signed = values.direction === "in" ? amount : -amount;

  try {
    if (!form.get("force")) {
      const { transactions } = await loadData();
      const close = near(transactions, when!, signed);
      const matches = transactions.filter((_, i) => close[i]);
      if (matches.length) {
        return {
          status: "confirm",
          values,
          message: `Your sheet already has ${matches.length} row(s) for ${fc(amount)} within ${LOGGED_WINDOW_DAYS} days of ${iso(when!)}. If one of these is it, it is already tracked.`,
          matches: matches.map((t) => [t.date === null ? "" : format(t.date, "Dow DD Mon"), t.description, t.category, fc(t.amount)]),
        };
      }
    }

    await appendRecords(TAB.transactions, [{
      "Transaction ID": "",
      Date: iso(when!),
      "Account ID": values.account,
      Description: values.description,
      Category: values.category,
      Amount: signed,
      Notes: LOGGED_BY_HAND,
    }], "Transaction ID");
  } catch (error) {
    return { status: "error", message: (error as Error).message, values };
  }

  revalidateTag(SHEET_TAG, { expire: 0 });
  return { status: "ok", message: `Logged ${fc(amount)} — ${values.description} (${values.category}, ${iso(when!)}).` };
}
