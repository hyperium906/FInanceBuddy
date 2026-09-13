/**
 * What was spent in a pay period, and what is left in each category.
 *
 * Three rules, each learned from a real statement:
 *
 * *Moving your own money is not spending.* A transfer leaves one balance and
 * arrives in another. Counted, it is usually the largest line on the page.
 *
 * *A refund does not reduce its category.* Returning a jacket must not free up
 * budget to buy another.
 *
 * *A reimbursement is the exception.* When everyone pays you back for the group
 * meal, the repaid part was never your spending. It nets off, floored at zero.
 *
 * Budgets are monthly in `_Budgets` and converted to a period's share through
 * 26 checks a year, never "half the monthly figure".
 */

import type { Day } from "./dates";
import type { Budget, Transaction } from "./models";
import { labelOf, lower, sum, toNumber } from "./money";
import { perPeriod, type PayPeriod } from "./periods";
import { parts } from "./dates";

export const MOVEMENT_CATEGORIES = new Set(["transfer", "savings"]);
export const NETTING_CATEGORIES = new Set(["reimbursable"]);

/** Amber from 80% of an allowance; red strictly above 100%. */
export const AMBER_AT = 0.8;

export type Status = "green" | "amber" | "red" | "none";

export class CategoryLine {
  constructor(readonly category: string, readonly spent: number, readonly allowance: number) {}

  get left(): number {
    return this.allowance - this.spent;
  }

  /** Unbudgeted is not a zero budget. */
  get budgeted(): boolean {
    return this.allowance > 0;
  }

  get used(): number | null {
    return this.budgeted ? this.spent / this.allowance : null;
  }

  get status(): Status {
    if (!this.budgeted) return "none";
    const share = this.spent / this.allowance;
    if (share > 1) return "red";
    return share >= AMBER_AT ? "amber" : "green";
  }

  /** Daily spend that exactly finishes the allowance. Today is still spendable. */
  pace(period: PayPeriod, today: Day): number | null {
    if (!this.budgeted || this.left <= 0) return null;
    return this.left / Math.max(period.remaining(today), 1);
  }
}

function usable(transactions: Transaction[], period: PayPeriod): Transaction[] {
  return transactions.filter(
    (t) => t.date !== null && period.contains(t.date) && !MOVEMENT_CATEGORIES.has(lower(t.category)),
  );
}

/** Money out per category in `period`, positive, dearest first (ties alphabetical). */
export function spentByCategory(transactions: Transaction[], period: PayPeriod): Array<{ category: string; spent: number }> {
  const rows = usable(transactions, period);
  const spend = new Map<string, number>();
  for (const t of rows) {
    if (t.amount < 0) spend.set(labelOf(t.category), (spend.get(labelOf(t.category)) ?? 0) - t.amount);
  }
  const repaid = new Map<string, number>();
  for (const t of rows) {
    if (t.amount > 0 && NETTING_CATEGORIES.has(lower(t.category))) {
      repaid.set(labelOf(t.category), (repaid.get(labelOf(t.category)) ?? 0) + t.amount);
    }
  }

  return [...spend.entries()]
    .map(([category, spent]) => ({ category, spent: Math.max(spent - (repaid.get(category) ?? 0), 0) }))
    .filter((row) => row.spent > 0)
    .sort((a, b) => b.spent - a.spent || (a.category < b.category ? -1 : 1));
}

/** Money genuinely earned in `period`. Transfers and repayments excluded. */
export function incomeIn(transactions: Transaction[], period: PayPeriod): number {
  return sum(
    usable(transactions, period)
      .filter((t) => t.amount > 0 && !NETTING_CATEGORIES.has(lower(t.category)))
      .map((t) => t.amount),
  );
}

export function spentIn(transactions: Transaction[], period: PayPeriod): number {
  return sum(spentByCategory(transactions, period).map((row) => row.spent));
}

/**
 * Each category's share of its monthly budget for one pay period, matched by
 * the month the **payday** falls in — so a period straddling the 1st draws on
 * one month's budget rather than being split at midnight.
 */
export function allowances(budgets: Budget[], period: PayPeriod): Record<string, number> {
  if (!budgets.length) return {};
  const { year, month } = parts(period.payday);
  const key = `${year}-${String(month).padStart(2, "0")}`;
  let rows = budgets.filter((b) => lower(b.month) === key);
  if (!rows.length) {
    // No budget for that month: use the latest month set, so a new month is
    // not silently unbudgeted everywhere.
    const months = [...new Set(budgets.map((b) => lower(b.month)).filter(Boolean))].sort();
    if (!months.length) return {};
    rows = budgets.filter((b) => lower(b.month) === months[months.length - 1]);
  }
  const monthly = new Map<string, number>();
  for (const row of rows) {
    const name = labelOf(row.category);
    monthly.set(name, (monthly.get(name) ?? 0) + toNumber(row.amount));
  }
  return Object.fromEntries([...monthly].map(([name, value]) => [name, perPeriod(value, period.cadence)]));
}

/**
 * Every category with spending or a budget in `period`, dearest first. A
 * budget nobody has touched is included — it is an allowance still available.
 */
export function categoryLines(transactions: Transaction[], budgets: Budget[], period: PayPeriod): CategoryLine[] {
  const spent = Object.fromEntries(spentByCategory(transactions, period).map((r) => [r.category, r.spent]));
  const budget = allowances(budgets, period);
  const names = [...new Set([...Object.keys(spent), ...Object.keys(budget)])].sort();
  return names
    .map((name) => new CategoryLine(name, spent[name] ?? 0, budget[name] ?? 0))
    .sort((a, b) => b.spent - a.spent || (a.category < b.category ? -1 : 1));
}

export class PeriodSummary {
  constructor(readonly period: PayPeriod, readonly income: number, readonly spent: number, readonly committed = 0) {}

  get net(): number {
    return this.income - this.spent;
  }

  get free(): number {
    return this.income - this.spent - this.committed;
  }
}

export function summarise(transactions: Transaction[], period: PayPeriod, committed = 0): PeriodSummary {
  return new PeriodSummary(period, incomeIn(transactions, period), spentIn(transactions, period), committed);
}
