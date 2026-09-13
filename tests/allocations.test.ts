/**
 * Standing allocation rules and what is still to be moved. The central fact:
 * a fixed rule in `_Allocations` is a **monthly** amount. An earlier version
 * read $900 of rules as $900 per check and invented an over-commitment.
 */

import { describe, expect, it } from "vitest";
import * as A from "../src/lib/allocations";
import * as P from "../src/lib/periods";
import { d, rule, tx } from "./helpers";

const PERIOD = P.currentPeriod(d("2026-09-04"), "biweekly", d("2026-09-12"));
const PAYCHECK = 2065.2;

const REAL = [
  rule("Student Loan", 300, 0, "Student Loan"),
  rule("Chase Savings", 200, 0, "chase_savings"),
  rule("Roth IRA", 100, 0, "fidelity_roth"),
  rule("Trading & Crypto", 100, 0, "fidelity_crypto"),
  rule("Public", 100, 0, "Public"),
  rule("Alpaca", 100, 0, "Alpaca"),
  rule("Tithing", 0, 10, "Giving"),
];

const moveFor = (bucket: string, ledger: ReturnType<typeof tx>[]) =>
  A.moves(REAL, PAYCHECK, PERIOD, ledger).find((m) => m.rule.bucket === bucket)!;

describe("monthly, not per-paycheck", () => {
  it("a fixed rule comes out whole on the first check", () => {
    const r = new A.Rule("Student Loan", 300);
    expect(r.due(PAYCHECK, true)).toBeCloseTo(300);
    expect(r.due(PAYCHECK, false)).toBe(0);
  });

  it("the real rule set totals what the workbook says", () => {
    const fixed = A.rules(REAL).filter((r) => r.kind === "fixed");
    expect(fixed.reduce((s, r) => s + r.monthly, 0)).toBeCloseTo(900);
    expect(fixed.reduce((s, r) => s + r.due(PAYCHECK, false), 0)).toBe(0);
  });

  it("the first check carries the transfers", () => {
    const p = A.plan(REAL, PAYCHECK, PERIOD, null, true);
    expect(p.due).toBeCloseTo(1106.52, 1);
    expect(p.unallocated).toBeCloseTo(958.68, 1);
  });

  it("the second check carries only the percentage, and lists nothing at zero", () => {
    const p = A.plan(REAL, PAYCHECK, PERIOD, null, false);
    expect(p.due).toBeCloseTo(206.52, 1);
    expect(p.moves.map((m) => m.rule.bucket)).toEqual(["Tithing"]);
    expect(p.moves.every((m) => m.due > 0)).toBe(true);
  });
});

describe("percentages", () => {
  it("apply to every check and scale with it", () => {
    const r = new A.Rule("Tithing", 0, 10);
    expect(r.due(PAYCHECK, true)).toBeCloseTo(206.52);
    expect(r.due(PAYCHECK, false)).toBeCloseTo(206.52);
    expect(r.due(5332.7, true)).toBeCloseTo(533.27);
  });

  it("are listed first", () => {
    expect(A.rules(REAL)[0].bucket).toBe("Tithing");
  });
});

describe("reading the sheet", () => {
  it("rows with a month are history, not rules", () => {
    expect(A.rules([rule("Savings", 200), rule("Old", 999, 0, "", "2026-08")]).map((r) => r.bucket)).toEqual(["Savings"]);
  });

  it("a history-only sheet falls back to its latest month", () => {
    const rows = [rule("Old", 100, 0, "", "2026-07"), rule("Recent", 200, 0, "", "2026-08")];
    expect(A.rules(rows).map((r) => r.bucket)).toEqual(["Recent"]);
  });

  it("empty and valueless rows are skipped", () => {
    expect(A.rules([rule("", 100), rule("Zero", 0), rule("Real", 50)]).map((r) => r.bucket)).toEqual(["Real"]);
  });

  it("an empty sheet is no rules, not an error", () => {
    expect(A.rules([])).toEqual([]);
    expect(A.plan([], PAYCHECK, PERIOD).due).toBe(0);
  });
});

describe("detecting what has already moved", () => {
  it("a transfer naming the account counts", () => {
    const move = moveFor("Chase Savings", [tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200)]);
    expect(move.moved).toBeCloseTo(200);
    expect(move.done).toBe(true);
  });

  it("an unmatched transfer leaves the move outstanding", () => {
    const move = moveFor("Chase Savings", [tx("2026-09-05", "Online Transfer to somewhere", "Transfer", -200)]);
    expect(move.moved).toBe(0);
    expect(move.done).toBe(false);
  });

  it("only movement categories count", () => {
    expect(moveFor("Alpaca", [tx("2026-09-05", "Alpaca cafe", "Dining", -46.15)]).moved).toBe(0);
  });

  it("a move outside the period does not count", () => {
    expect(moveFor("Chase Savings", [tx("2026-09-02", "Online Transfer to chase_savings", "Transfer", -200)]).moved).toBe(0);
  });

  it("a gift filed under Giving fulfils the tithe, and nothing else", () => {
    const ledger = [tx("2026-09-06", "HOPE CHURCH 478-9537898 GA", "Giving", -206.52)];
    expect(moveFor("Tithing", ledger).done).toBe(true);
    expect(moveFor("Chase Savings", [tx("2026-09-06", "Tithe", "Giving", -200)]).moved).toBe(0);
  });

  it("the matching credit is not counted twice", () => {
    const move = moveFor("Chase Savings", [
      tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200),
      tx("2026-09-05", "Online Transfer from chk", "Transfer", 200, "chase_savings"),
    ]);
    expect(move.moved).toBeCloseTo(200);
  });

  it("moving extra is not a debt and is reported", () => {
    const move = new A.Move(new A.Rule("Savings", 200), 200, 250);
    expect([move.outstanding, move.done, move.overshot]).toEqual([0, true, 50]);
  });

  it("the plan totals what is left to do", () => {
    const p = A.plan(REAL, PAYCHECK, PERIOD, [tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200)]);
    expect(p.moved).toBeCloseTo(200);
    expect(p.outstanding).toBeCloseTo(p.due - 200);
    expect(p.complete).toBe(false);
  });

  it("rules claiming more than the check go negative", () => {
    expect(A.plan([rule("Everything", 10000)], PAYCHECK, PERIOD, null, true).unallocated).toBeLessThan(0);
  });
});
