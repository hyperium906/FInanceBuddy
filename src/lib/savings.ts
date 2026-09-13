/**
 * Savings buckets and dated goals, which are the same subject seen twice.
 *
 * Money is already put aside every month, and a goal is not a separate pot
 * beside that — it is a claim on the same flow. So the standing allocations
 * are the savings rate, the account balances are what has been saved, and a
 * dated goal is measured against both.
 *
 * *Not every allocation is saving.* The loan payment is repayment and the
 * tithe is giving; counting them would overstate the rate by half.
 *
 * *A goal with no date is not behind.* Nothing has been measured yet.
 */

import type { Day } from "./dates";
import { rules as allocationRules } from "./allocations";
import type { Account, Allocation, GoalRow } from "./models";
import { sum } from "./money";
import { currentPeriod, DEFAULT_CADENCE, perMonth, perPeriod, shift } from "./periods";

const DEBT_WORDS = ["loan", "debt", "card", "mortgage", "payoff", "credit"];
const GIVING_WORDS = ["tith", "giving", "church", "offering", "donat", "charity"];
/** Put aside, but not spendable for decades — so never what a car is measured against. */
const RETIREMENT_WORDS = ["roth", "ira", "401k", "401(k)", "403b", "pension", "retirement", "tsp"];

export type BucketKind = "savings" | "retirement" | "debt" | "giving";

export function classify(bucket: string, accountId = ""): BucketKind {
  const haystack = `${bucket} ${accountId}`.toLowerCase();
  if (DEBT_WORDS.some((w) => haystack.includes(w))) return "debt";
  if (GIVING_WORDS.some((w) => haystack.includes(w))) return "giving";
  if (RETIREMENT_WORDS.some((w) => haystack.includes(w))) return "retirement";
  return "savings";
}

export interface Bucket {
  name: string;
  monthly: number;
  kind: BucketKind;
  accountId: string;
  /** Null when nothing tracks it — unknown, which is not the same as zero. */
  balance: number | null;
}

export const tracked = (bucket: Bucket) => bucket.balance !== null;

export const perCheck = (bucket: Bucket, cadence: unknown = DEFAULT_CADENCE) => perPeriod(bucket.monthly, cadence);

const fold = (text: unknown) => String(text ?? "").trim().toLowerCase().replace(/ /g, "_");

/** Every standing allocation, classified, with its balance where known. */
export function buckets(allocations: Allocation[], accounts: Account[], paycheck = 0, cadence: unknown = DEFAULT_CADENCE): Bucket[] {
  // Buckets are named by hand and accounts given ids, so "Public" has to find
  // "public" and "Public Brokerage". First spelling wins.
  const known = new Map<string, number>();
  for (const account of accounts) {
    for (const candidate of [account.id, account.name]) {
      const key = fold(candidate);
      if (key && !known.has(key)) known.set(key, account.balance || 0);
    }
  }

  return allocationRules(allocations).map((rule) => {
    const monthly = rule.kind === "percent" ? perMonth(rule.due(paycheck, true), cadence) : rule.monthly;
    let balance: number | null = null;
    for (const candidate of [rule.accountId, rule.bucket]) {
      const key = fold(candidate);
      if (known.has(key)) {
        balance = known.get(key)!;
        break;
      }
    }
    return { name: rule.bucket, monthly, kind: classify(rule.bucket, rule.accountId), accountId: rule.accountId, balance };
  });
}

/** Monthly total into reachable savings: no repayment, giving, or retirement. */
export const savingsRate = (items: Bucket[]) => sum(items.filter((b) => b.kind === "savings").map((b) => b.monthly));
export const retirementRate = (items: Bucket[]) => sum(items.filter((b) => b.kind === "retirement").map((b) => b.monthly));
export const retirementTotal = (items: Bucket[]) =>
  sum(items.filter((b) => b.kind === "retirement" && tracked(b)).map((b) => b.balance ?? 0));
export const savedTotal = (items: Bucket[]) =>
  sum(items.filter((b) => b.kind === "savings" && tracked(b)).map((b) => b.balance ?? 0));
export const untracked = (items: Bucket[]) => items.filter((b) => b.kind === "savings" && !tracked(b));

// --------------------------------------------------------------------------
// Dated goals
// --------------------------------------------------------------------------

export class Goal {
  constructor(
    readonly name: string,
    readonly target: number,
    readonly saved = 0,
    readonly deadline: Day | null = null,
    readonly goalId = "",
    readonly bucket = "",
    readonly notes = "",
  ) {}

  get remaining(): number {
    return Math.max(this.target - this.saved, 0);
  }

  get met(): boolean {
    return this.remaining <= 0;
  }

  get progress(): number {
    return this.target > 0 ? Math.min(this.saved / this.target, 1) : 0;
  }
}

export function readGoals(rows: GoalRow[]): Goal[] {
  return rows
    .filter((row) => String(row.name ?? "").trim())
    .map((row) => new Goal(row.name.trim(), row.targetAmount || 0, row.savedAmount || 0, row.targetDate, row.id, row.bucket, row.notes));
}

/**
 * Paychecks landing between now and `deadline`, inclusive. Counted, not
 * divided: "three months away" is six checks or seven depending on the paydays.
 */
export function checksUntil(deadline: Day, anchor: Day, cadence: unknown, today: Day): number {
  let period = currentPeriod(anchor, cadence, today);
  let count = 0;
  while (period.start <= deadline) {
    count += 1;
    period = shift(period, 1);
  }
  return count;
}

export type GoalVerdict = "met" | "on-track" | "stretch" | "missing" | "no-date";

export class GoalPlan {
  constructor(
    readonly goal: Goal,
    readonly checksLeft: number,
    readonly availablePerCheck: number,
    readonly currentRatePerCheck = 0,
    readonly today: Day = 0,
  ) {}

  get requiredPerCheck(): number {
    if (this.goal.met) return 0;
    if (this.checksLeft <= 0) return this.goal.remaining;
    return this.goal.remaining / this.checksLeft;
  }

  get feasible(): boolean {
    return this.requiredPerCheck <= this.availablePerCheck;
  }

  get shortfallPerCheck(): number {
    return Math.max(this.requiredPerCheck - this.availablePerCheck, 0);
  }

  /** Checks needed at what is already being saved, or null if never. */
  get checksAtCurrentRate(): number | null {
    if (this.goal.met) return 0;
    if (this.currentRatePerCheck <= 0) return null;
    return Math.ceil(this.goal.remaining / this.currentRatePerCheck);
  }

  get lands(): Day | null {
    const checks = this.checksAtCurrentRate;
    return checks === null ? null : this.today + checks * 14;
  }

  get verdict(): GoalVerdict {
    if (this.goal.met) return "met";
    if (this.goal.deadline === null) return "no-date";
    if (this.feasible) return "on-track";
    return this.shortfallPerCheck <= this.availablePerCheck ? "stretch" : "missing";
  }
}

export function planFor(
  goal: Goal,
  anchor: Day,
  availablePerCheck: number,
  currentRatePerCheck = 0,
  cadence: unknown = DEFAULT_CADENCE,
  today: Day,
): GoalPlan {
  const left = goal.deadline !== null ? checksUntil(goal.deadline, anchor, cadence, today) : 0;
  return new GoalPlan(goal, left, availablePerCheck, currentRatePerCheck, today);
}
