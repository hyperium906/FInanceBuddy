/**
 * Which paycheck carries which obligation. On this income the rent check is
 * short every month, and the first check has to carry the difference forward.
 */

import { describe, expect, it } from "vitest";
import * as K from "../src/lib/commitments";
import * as P from "../src/lib/periods";
import { bill, d, rule } from "./helpers";

const ANCHOR = d("2026-09-11");
const TODAY = d("2026-09-12");
const PAYCHECK = 2065.2;

const RECURRING = [
  bill("Google One", 19.99, "2026-09-17"),
  bill("Electricity", 40, "2026-09-18", { category: "Utilities" }),
  bill("Anthropic", 20, "2026-09-24"),
  bill("Software / Music", 2.99, "2026-09-27"),
  bill("Spotify", 12.99, "2026-09-30"),
  bill("Rent", 1875.95, "2026-09-01", { category: "Housing" }),
  bill("Water", 40, "2026-09-01", { category: "Utilities" }),
  bill("Amazon", 14.99, "2026-09-01"),
  bill("Uppbeat", 8.99, "2026-09-08"),
];

const ALLOCATIONS = [
  rule("Student Loan", 300, 0, "Student Loan"), rule("Chase Savings", 200, 0, "Chase Savings"),
  rule("Roth IRA", 100, 0, "Roth IRA"), rule("Trading & Crypto", 100, 0, "Trading & Crypto"),
  rule("Public", 100, 0, "Public"), rule("Alpaca", 100, 0, "Alpaca"),
  rule("Tithing", 0, 10, "Tithing"),
];

const check = (step = 0) =>
  K.forCheck(P.shift(P.currentPeriod(ANCHOR, "biweekly", TODAY), step), PAYCHECK, RECURRING, ALLOCATIONS, ANCHOR, "biweekly");

describe("which check is which", () => {
  it("paydays in a month are computed, not assumed", () => {
    expect(K.paydaysInMonth(2026, 9, ANCHOR, "biweekly")).toEqual([d("2026-09-11"), d("2026-09-25")]);
    expect(K.paydaysInMonth(2026, 10, ANCHOR, "biweekly")).toEqual([d("2026-10-09"), d("2026-10-23")]);
    expect(K.paydaysInMonth(2026, 7, d("2026-07-03"), "biweekly")).toHaveLength(3);
  });

  it("the ordinal says which check of the month", () => {
    expect([check(0).which, check(0).of]).toEqual([1, 2]);
    expect([check(1).which, check(1).of]).toEqual([2, 2]);
    expect([check(2).which, check(2).of]).toEqual([1, 2]);
  });
});

describe("assignment", () => {
  it("a bill goes to the last check before it is due", () => {
    expect(check(0).bills.map((b) => b.name)).not.toContain("Rent");
    expect(check(1).bills.map((b) => b.name)).toContain("Rent");
  });

  it("bills land in exactly one check", () => {
    const names = [...check(0).bills, ...check(1).bills].map((b) => b.name);
    expect(new Set(names).size).toBe(names.length);
    expect(new Set(names)).toEqual(new Set(RECURRING.map((r) => r.name)));
  });

  it("transfers go to the first check only, never prorated", () => {
    expect(check(0).movesTotal).toBeCloseTo(1106.52, 1);
    expect(check(1).movesTotal).toBeCloseTo(206.52, 1);
    const due = Object.fromEntries(check(0).allocation!.moves.map((m) => [m.rule.bucket, m.due]));
    expect(due["Student Loan"]).toBeCloseTo(300);
  });
});

describe("the consequence", () => {
  it("the first check is comfortable", () => {
    const first = check(0);
    expect(first.billsTotal).toBeCloseTo(79.98, 1);
    expect(first.left).toBeCloseTo(878.7, 1);
    expect(first.short).toBe(false);
  });

  it("the rent check is short, and named as such", () => {
    const second = check(1);
    expect(second.billsTotal).toBeCloseTo(1955.91, 1);
    expect(second.left).toBeCloseTo(-97.23, 1);
    expect(second.short).toBe(true);
    expect(second.isRentCheck).toBe(true);
    expect(second.dominant?.name).toBe("Rent");
    expect(check(0).dominant).toBeNull();
  });

  it("a check with no bills is not a rent check", () => {
    const plan = K.forCheck(P.currentPeriod(ANCHOR, "biweekly", TODAY), PAYCHECK, [], ALLOCATIONS, ANCHOR);
    expect(plan.isRentCheck).toBe(false);
    expect(plan.billsTotal).toBe(0);
  });

  it("the month ahead shows the rent check coming", () => {
    const plans = K.monthAhead(ANCHOR, PAYCHECK, RECURRING, ALLOCATIONS, "biweekly", TODAY, 4);
    expect(plans.map((p) => p.short)).toEqual([false, true, false, true]);
  });

  it("per day spreads what is left over the period", () => {
    expect(check(0).perDay).toBeCloseTo(check(0).left / 14, 2);
  });

  it("an empty sheet produces an empty plan", () => {
    const plan = K.forCheck(P.currentPeriod(ANCHOR, "biweekly", TODAY), PAYCHECK, [], [], ANCHOR);
    expect(plan.committed).toBe(0);
    expect(plan.left).toBeCloseTo(PAYCHECK);
  });
});

describe("running balance", () => {
  const threaded = () => K.running(K.monthAhead(ANCHOR, PAYCHECK, RECURRING, ALLOCATIONS, "biweekly", TODAY, 4));

  it("the surplus carries into the rent check, which needs it but is funded", () => {
    const [first, second] = threaded();
    expect(first.opening).toBe(0);
    expect(second.opening).toBeCloseTo(first.closing);
    expect(first.needsCarry).toBe(0);
    expect(second.needsCarry).toBeGreaterThan(0);
    expect(second.unfunded).toBe(0);
  });

  it("the balance never goes negative on this income", () => {
    expect(threaded().every((r) => r.closing > 0)).toBe(true);
  });

  it("a balance that never recovers is flagged unfunded", () => {
    const rows = K.running(K.monthAhead(ANCHOR, 300, RECURRING, ALLOCATIONS, "biweekly", TODAY, 2));
    expect(rows.some((r) => r.unfunded > 0)).toBe(true);
  });

  it("an opening balance is respected", () => {
    const [row] = K.running(K.monthAhead(ANCHOR, PAYCHECK, RECURRING, ALLOCATIONS, "biweekly", TODAY, 1), 500);
    expect(row.opening).toBe(500);
    expect(row.closing).toBeCloseTo(500 + row.check.left);
  });
});
