/**
 * Standing rules for where each paycheck's money goes, and what is still owed.
 *
 * **Fixed rules are monthly amounts and come out whole, from one check.** A
 * $300 student loan payment is one $300 transfer a month — not $138.46 skimmed
 * off each paycheck. It is charged in full to the **first paycheck of its
 * month** and is zero on the others. That is forced by the money: the second
 * check is consumed by rent, so the standing rules have nowhere else to come
 * from.
 *
 * **Percentage rules apply to every check.** A 10% tithe is 10% of what
 * actually arrived, each time it arrives.
 */

import type { Allocation, Transaction } from "./models";
import { labelOf, lower, round2, sum } from "./money";
import type { PayPeriod } from "./periods";
import { classify } from "./savings";
import { MOVEMENT_CATEGORIES } from "./spending";

/** A move within a cent of what is due is done; rounding must not keep it open forever. */
export const TOLERANCE = 0.01;

export class Rule {
  constructor(
    readonly bucket: string,
    /** Dollars per month, for a fixed rule. */
    readonly monthly = 0,
    /** Percent points (10 == 10%), for a percent rule. */
    readonly rate = 0,
    readonly accountId = "",
  ) {}

  get kind(): "percent" | "fixed" {
    return this.rate ? "percent" : "fixed";
  }

  /** What this rule claims from one paycheck. */
  due(paycheck: number, firstCheck = true): number {
    if (this.rate) return round2((paycheck * this.rate) / 100);
    return firstCheck ? round2(this.monthly) : 0;
  }
}

/**
 * The standing rules out of `_Allocations`, percentages first — the order the
 * arithmetic happens in. Rows carrying a Month are history, not rules; a sheet
 * that is only history falls back to its most recent month.
 */
export function rules(allocations: Allocation[]): Rule[] {
  if (!allocations.length) return [];
  let frame = allocations.filter((row) => lower(row.month) === "");
  if (!frame.length) {
    const months = [...new Set(allocations.map((row) => lower(row.month)).filter(Boolean))].sort();
    const latest = months[months.length - 1];
    frame = latest ? allocations.filter((row) => lower(row.month) === latest) : allocations;
  }

  const out: Rule[] = [];
  for (const row of frame) {
    const bucket = labelOf(row.bucket, "");
    if (!bucket) continue;
    const rate = row.percent || 0;
    const amount = row.amount || 0;
    if (!rate && !amount) continue;
    out.push(new Rule(bucket, rate ? 0 : amount, rate, labelOf(row.accountId, "")));
  }
  // Stable: within each kind the sheet's own order stands.
  return [...out.filter((r) => r.kind === "percent"), ...out.filter((r) => r.kind === "fixed")];
}

export class Move {
  constructor(readonly rule: Rule, readonly due: number, readonly moved = 0) {}

  /** Still to transfer. Never negative — moving extra is not a debt. */
  get outstanding(): number {
    return Math.max(this.due - this.moved, 0);
  }

  get done(): boolean {
    return this.outstanding <= TOLERANCE;
  }

  get overshot(): number {
    return Math.max(this.moved - this.due, 0);
  }
}

/**
 * Money already moved toward `rule` inside `period`.
 *
 * Deliberately narrow: a movement-category row whose account or description
 * names the bucket or its account. A move wrongly shown as done is worse than
 * one shown as outstanding — the second gets checked, the first does not.
 */
function movedInto(transactions: Transaction[], period: PayPeriod, rule: Rule): number {
  const needles = [rule.bucket, rule.accountId].filter(Boolean).map((n) => n.toLowerCase());
  const giving = classify(rule.bucket, rule.accountId) === "giving";

  return sum(
    transactions
      .filter((t) => {
        if (t.date === null || !period.contains(t.date)) return false;
        // Only money leaving: the matching credit is the same dollar arriving.
        if (!(t.amount < 0)) return false;
        const category = lower(t.category);
        const account = lower(t.accountId);
        const description = lower(t.description);
        const named = needles.includes(account) || needles.some((n) => description.includes(n));
        if (MOVEMENT_CATEGORIES.has(category) && named) return true;
        // A gift never names the rule it fulfils — the statement reads "HOPE
        // CHURCH", not "Tithing" — so for a giving rule the category is the evidence.
        return giving && category === "giving";
      })
      .map((t) => -t.amount),
  );
}

/** Every rule with what it claims and what has gone. Rules claiming nothing are dropped. */
export function moves(
  allocations: Allocation[],
  paycheck: number,
  period: PayPeriod,
  transactions: Transaction[] | null = null,
  firstCheck = true,
): Move[] {
  const out: Move[] = [];
  for (const rule of rules(allocations)) {
    const due = rule.due(paycheck, firstCheck);
    if (due <= 0) continue;
    out.push(new Move(rule, due, transactions ? movedInto(transactions, period, rule) : 0));
  }
  return out;
}

export class Plan {
  constructor(readonly paycheck: number, readonly moves: Move[]) {}

  get due(): number {
    return sum(this.moves.map((m) => m.due));
  }

  get moved(): number {
    return sum(this.moves.map((m) => Math.min(m.moved, m.due)));
  }

  get outstanding(): number {
    return sum(this.moves.map((m) => m.outstanding));
  }

  /** What the check leaves once every rule is satisfied. Negative is stated, not capped. */
  get unallocated(): number {
    return this.paycheck - this.due;
  }

  get complete(): boolean {
    return this.moves.every((m) => m.done);
  }
}

export function plan(
  allocations: Allocation[],
  paycheck: number,
  period: PayPeriod,
  transactions: Transaction[] | null = null,
  firstCheck = true,
): Plan {
  return new Plan(paycheck, moves(allocations, paycheck, period, transactions, firstCheck));
}
