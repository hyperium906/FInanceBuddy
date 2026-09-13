/**
 * Savings buckets and dated goals: not every standing allocation is saving,
 * and a goal with no history is not behind.
 */

import { describe, expect, it } from "vitest";
import type { Account } from "../src/lib/models";
import * as S from "../src/lib/savings";
import { d, rule } from "./helpers";

const ANCHOR = d("2026-09-11");
const TODAY = d("2026-09-12");
const PAYCHECK = 2065.2;

const ALLOCATIONS = [
  rule("Student Loan", 300, 0, "Student Loan"), rule("Chase Savings", 200, 0, "chase_savings"),
  rule("Roth IRA", 100, 0, "fidelity_roth"), rule("Trading & Crypto", 100, 0, "fidelity_crypto"),
  rule("Public", 100, 0, "Public"), rule("Alpaca", 100, 0, "Alpaca"), rule("Tithing", 0, 10, "Giving"),
];

const account = (id: string, name: string, type: string, balance: number): Account =>
  ({ id, name, type, institution: "", balance, currency: "USD", lastUpdated: null });

const ACCOUNTS = [
  account("chase_savings", "Chase Savings", "savings", 1300),
  account("fidelity_roth", "Roth IRA", "retirement", 118.52),
  account("fidelity_crypto", "Crypto", "crypto", 54.01),
  // Lower-cased while the buckets say "Public"/"Alpaca".
  account("public", "Public Brokerage", "investment", 50),
  account("alpaca", "Alpaca", "investment", 0),
];

const pots = () => S.buckets(ALLOCATIONS, ACCOUNTS, PAYCHECK);

describe("not every allocation is saving", () => {
  it.each([
    ["Student Loan", "debt"], ["Car Loan", "debt"], ["Credit Card", "debt"],
    ["Tithing", "giving"], ["Church", "giving"], ["Charity", "giving"],
    ["Roth IRA", "retirement"], ["401k", "retirement"], ["Pension", "retirement"],
    ["Chase Savings", "savings"], ["Alpaca", "savings"], ["Trading & Crypto", "savings"],
  ])("%s is %s", (bucket, expected) => {
    expect(S.classify(bucket)).toBe(expected);
  });

  it("the savings rate excludes repayment, giving and retirement", () => {
    expect(S.savingsRate(pots())).toBeCloseTo(500);
    expect(S.retirementRate(pots())).toBeCloseTo(100);
    expect(S.retirementTotal(pots())).toBeCloseTo(118.52);
    expect(S.savedTotal(pots())).toBeCloseTo(1300 + 54.01 + 50);
  });

  it("a bucket finds its account whatever the casing, and a zero balance is tracked", () => {
    expect(pots().find((b) => b.name === "Public")!.balance).toBeCloseTo(50);
    const alpaca = pots().find((b) => b.name === "Alpaca")!;
    expect([S.tracked(alpaca), alpaca.balance]).toEqual([true, 0]);
  });

  it("the tithe is priced off the paycheck", () => {
    const tithe = pots().find((b) => b.name === "Tithing")!;
    expect(tithe.kind).toBe("giving");
    expect(tithe.monthly).toBeCloseTo((2065.2 * 0.1 * 26) / 12, 1);
  });

  it("an untracked bucket is not counted as empty", () => {
    const [bucket] = S.buckets([rule("Somewhere Else", 100)], ACCOUNTS, PAYCHECK);
    expect(bucket.balance).toBeNull();
    expect(S.savedTotal([bucket])).toBe(0);
    expect(S.untracked([bucket]).map((b) => b.name)).toEqual(["Somewhere Else"]);
  });
});

describe("counting checks, not months", () => {
  it("counts the paychecks until a deadline", () => {
    expect(S.checksUntil(d("2026-12-31"), ANCHOR, "biweekly", TODAY)).toBe(8);
    expect(S.checksUntil(d("2026-08-01"), ANCHOR, "biweekly", TODAY)).toBe(0);
  });
});

describe("what a goal asks of each check", () => {
  const car = (target: number, saved = 0, deadline = "2026-12-31") => new S.Goal("Car", target, saved, d(deadline));
  const plan = (target: number, saved = 0, spare = 400, rate = 277) =>
    S.planFor(car(target, saved), ANCHOR, spare, rate, "biweekly", TODAY);

  it("spreads the requirement over the checks that remain, after what is saved", () => {
    expect(plan(4000).requiredPerCheck).toBeCloseTo(500);
    expect(plan(4000, 1500).requiredPerCheck).toBeCloseTo(2500 / 8);
  });

  it("says whether it is reachable, and how short", () => {
    expect(plan(2400).verdict).toBe("on-track");
    const far = plan(8000);
    expect(far.feasible).toBe(false);
    expect(["stretch", "missing"]).toContain(far.verdict);
    expect(far.shortfallPerCheck).toBeCloseTo(600);
  });

  it("a met goal asks for nothing", () => {
    const met = plan(1000, 1200);
    expect([met.goal.met, met.requiredPerCheck, met.verdict, met.goal.progress]).toEqual([true, 0, "met", 1]);
  });

  it("a goal with no date is not behind; a past deadline wants it all now", () => {
    const someday = S.planFor(new S.Goal("Someday", 5000), ANCHOR, 400, 277, "biweekly", TODAY);
    expect([someday.verdict, someday.checksLeft]).toEqual(["no-date", 0]);
    const late = S.planFor(car(4000, 0, "2026-08-01"), ANCHOR, 400, 277, "biweekly", TODAY);
    expect([late.checksLeft, late.requiredPerCheck]).toEqual([0, 4000]);
  });

  it("the current rate projects a landing date, or never", () => {
    expect(plan(4000, 0, 400, 277).checksAtCurrentRate).toBe(15);
    expect(plan(4000, 0, 400, 277).lands).not.toBeNull();
    expect(plan(4000, 0, 400, 0).lands).toBeNull();
  });

  it("goals are read from the sheet", () => {
    const [goal] = S.readGoals([{ id: "g1", name: "Car", targetAmount: 4000, savedAmount: 500, targetDate: d("2026-12-31"), bucket: "Chase Savings", accountId: "", notes: "" }]);
    expect(goal.remaining).toBeCloseTo(3500);
    expect(goal.progress).toBeCloseTo(0.125);
    expect(S.readGoals([])).toEqual([]);
  });
});
