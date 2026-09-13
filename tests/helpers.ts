import { parseDay, type Day } from "../src/lib/dates";
import type { Allocation, Recurring, Transaction } from "../src/lib/models";

/** A day from `YYYY-MM-DD`, failing loudly on a typo rather than yielding null. */
export function d(text: string): Day {
  const value = parseDay(text);
  if (value === null) throw new Error(`bad test date ${text}`);
  return value;
}

export function tx(date: string, description: string, category: string, amount: number, accountId = "chk"): Transaction {
  return { id: `${date}-${Math.abs(amount)}`, date: d(date), accountId, description, category, amount, notes: "" };
}

export function rule(bucket: string, amount = 0, percent = 0, accountId = "", month = ""): Allocation {
  return { id: "", month, bucket, percent, amount, accountId, notes: "" };
}

export function bill(name: string, amount: number, nextDue: string, extra: Partial<Recurring> = {}): Recurring {
  return {
    id: name.slice(0, 4), name, category: "Subscriptions", amount: -Math.abs(amount), frequency: "monthly",
    nextDue: d(nextDue), accountId: "", active: true, merchant: "", estimated: false, confirmed: false,
    taxRate: 0, variable: false, ...extra,
  };
}
