/**
 * What one paycheck has to cover, in whole monthly amounts.
 *
 * The two checks in a month do not have the same job. One pays the rent and is
 * almost entirely gone before it arrives; the other carries the standing
 * transfers and most of the spending. Averaging them hides the fact that one
 * check is already spent. So nothing is prorated — each obligation is charged
 * **whole** to one paycheck:
 *
 * - *A bill goes to the last check before it is due.* Rent due on the 1st is
 *   paid out of the check on the 25th, which is what the statement shows.
 * - *A standing transfer goes to the first check of its month.*
 * - *A percentage rule applies to every check.*
 */

import { plan as allocationPlan, type Plan } from "./allocations";
import { budgetedAmounts, observe } from "./billing";
import { day, daysInMonth, type Day, parts } from "./dates";
import type { Allocation, Recurring, Transaction } from "./models";
import { sum } from "./money";
import { currentPeriod, DEFAULT_CADENCE, periodContaining, shift, type PayPeriod } from "./periods";
import { upcoming } from "./recurring";

/** Every payday in a calendar month. Usually two; about twice a year, three. */
export function paydaysInMonth(year: number, month: number, anchor: Day, cadence: unknown = DEFAULT_CADENCE): Day[] {
  const start = day(year, month, 1);
  const end = day(year, month, daysInMonth(year, month));
  let period = periodContaining(start, anchor, cadence);
  if (period.start < start) period = shift(period, 1);
  const out: Day[] = [];
  while (period.start <= end) {
    out.push(period.start);
    period = shift(period, 1);
  }
  return out;
}

/** Which check of its month this is, and how many the month holds: `[1, 2]`. */
export function ordinal(period: PayPeriod, anchor: Day, cadence: unknown = DEFAULT_CADENCE): [number, number] {
  const { year, month } = parts(period.payday);
  const days = paydaysInMonth(year, month, anchor, cadence);
  const index = days.indexOf(period.payday);
  return index >= 0 ? [index + 1, days.length] : [1, Math.max(days.length, 1)];
}

export class Bill {
  constructor(
    readonly name: string,
    /** What leaves the account, tax included. */
    readonly amount: number,
    readonly due: Day,
    readonly category = "",
    readonly listed = 0,
    /** Amount taken from the dearest charge seen. */
    readonly metered = false,
  ) {}

  get taxed(): boolean {
    return !this.metered && Boolean(this.listed) && Math.abs(this.amount - this.listed) >= 0.01;
  }
}

/** Charges due between this payday and the day before the next — the assignment rule itself. */
export function billsIn(recurring: Recurring[], period: PayPeriod, budgeted: Record<string, number> = {}): Bill[] {
  return upcoming(recurring, period.start, period.days - 1).map(
    (row) =>
      new Bill(row.name, budgeted[row.name] ?? row.amount, row.due, row.category, row.listed, row.name in budgeted),
  );
}

export class CheckPlan {
  constructor(
    readonly period: PayPeriod,
    readonly paycheck: number,
    readonly which: number,
    readonly of: number,
    readonly bills: Bill[] = [],
    readonly allocation: Plan | null = null,
  ) {}

  get billsTotal(): number {
    return sum(this.bills.map((b) => b.amount));
  }

  get movesTotal(): number {
    return this.allocation ? this.allocation.due : 0;
  }

  /** Bills plus standing transfers — spoken for before any spending. */
  get committed(): number {
    return this.billsTotal + this.movesTotal;
  }

  get left(): number {
    return this.paycheck - this.committed;
  }

  /**
   * Whether this check fails to cover its own obligations. Not a problem on
   * its own: the rent check never does, and the check before it runs a
   * surplus so it can be carried. `running` answers whether the month balances.
   */
  get short(): boolean {
    return this.left < 0;
  }

  get perDay(): number {
    return this.left / Math.max(this.period.days, 1);
  }

  /** One bill takes half the check or more — say so, rather than leave a big number unexplained. */
  get isRentCheck(): boolean {
    if (!this.bills.length || this.paycheck <= 0) return false;
    return Math.max(...this.bills.map((b) => b.amount)) / this.paycheck >= 0.5;
  }

  get dominant(): Bill | null {
    if (!this.isRentCheck) return null;
    return this.bills.reduce((a, b) => (b.amount > a.amount ? b : a));
  }
}

export function forCheck(
  period: PayPeriod,
  paycheck: number,
  recurring: Recurring[],
  allocations: Allocation[],
  anchor: Day,
  cadence: unknown = DEFAULT_CADENCE,
  transactions: Transaction[] | null = null,
  today: Day = period.start,
): CheckPlan {
  const [which, of] = ordinal(period, anchor, cadence);
  const budgeted = transactions?.length ? budgetedAmounts(recurring, observe(recurring, transactions, today)) : {};
  return new CheckPlan(
    period,
    paycheck,
    which,
    of,
    billsIn(recurring, period, budgeted),
    allocationPlan(allocations, paycheck, period, transactions, which === 1),
  );
}

export class Running {
  constructor(readonly check: CheckPlan, readonly opening: number) {}

  get closing(): number {
    return this.opening + this.check.left;
  }

  /** How much of the opening balance this check consumes. A dependency, not a hole. */
  get needsCarry(): number {
    return this.check.short ? Math.min(Math.max(-this.check.left, 0), Math.max(this.opening, 0)) : 0;
  }

  /** What nothing has funded. This one is a problem. */
  get unfunded(): number {
    return this.closing < 0 ? -this.closing : 0;
  }
}

/**
 * Thread a balance through consecutive checks. Four checks with two flagged
 * short reads as an alarm every fortnight; threaded, they read as a surplus
 * check followed by a rent check that spends it.
 */
export function running(plans: CheckPlan[], opening = 0): Running[] {
  const out: Running[] = [];
  let balance = opening;
  for (const plan of plans) {
    const row = new Running(plan, balance);
    out.push(row);
    balance = row.closing;
  }
  return out;
}

/** The next few checks and what each has to carry, current one first. */
export function monthAhead(
  anchor: Day,
  paycheck: number,
  recurring: Recurring[],
  allocations: Allocation[],
  cadence: unknown = DEFAULT_CADENCE,
  today: Day,
  checks = 4,
): CheckPlan[] {
  const period = currentPeriod(anchor, cadence, today);
  return Array.from({ length: checks }, (_, step) =>
    forCheck(shift(period, step), paycheck, recurring, allocations, anchor, cadence),
  );
}
