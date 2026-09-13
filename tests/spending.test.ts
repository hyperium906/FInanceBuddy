/**
 * What counts as spending inside a pay period: transfers are not spending,
 * refunds do not refund a budget, and a reimbursement is the one exception.
 */

import { describe, expect, it } from "vitest";
import type { Budget } from "../src/lib/models";
import * as P from "../src/lib/periods";
import * as S from "../src/lib/spending";
import { d, tx } from "./helpers";

const TODAY = d("2026-09-12");
const PERIOD = P.currentPeriod(d("2026-09-04"), "biweekly", TODAY);

const budgets = (monthly: Record<string, number>): Budget[] =>
  Object.entries(monthly).map(([category, amount], i) => ({ id: `b${i}`, month: "2026-09", category, amount, notes: "" }));

const spent = (rows: ReturnType<typeof tx>[]) =>
  Object.fromEntries(S.spentByCategory(rows, PERIOD).map((r) => [r.category, Number(r.spent.toFixed(2))]));

describe("the period boundary", () => {
  it("only this period counts, both boundary days inside", () => {
    expect(spent([tx("2026-09-02", "Rent", "Housing", -1875.95), tx("2026-09-06", "Aldi", "Groceries", -40)])).toEqual({ Groceries: 40 });
    expect(spent([
      tx("2026-09-04", "payday purchase", "Dining", -10), tx("2026-09-17", "last day", "Dining", -5),
      tx("2026-09-18", "next period", "Dining", -99), tx("2026-09-03", "previous period", "Dining", -99),
    ])).toEqual({ Dining: 15 });
  });
});

describe("what counts", () => {
  it("a transfer is not spending or income", () => {
    const rows = [
      tx("2026-09-05", "Online transfer to savings", "Transfer", -1500),
      tx("2026-09-06", "Online transfer from savings", "Transfer", 1500),
      tx("2026-09-07", "Coffee", "Dining", -5),
    ];
    expect(spent(rows)).toEqual({ Dining: 5 });
    expect(S.incomeIn(rows, PERIOD)).toBe(0);
    expect(S.spentIn(rows, PERIOD)).toBeCloseTo(5);
  });

  it("a refund does not reduce its category", () => {
    expect(spent([tx("2026-09-05", "Jacket", "Shopping", -220), tx("2026-09-08", "Jacket returned", "Shopping", 220)])).toEqual({ Shopping: 220 });
  });

  it("a reimbursement nets off; repaid in full removes the line; overpaid is not income", () => {
    expect(spent([
      tx("2026-09-11", "Aldi - BBQ", "Reimbursable", -110.34),
      tx("2026-09-11", "Cash App from a friend", "Reimbursable", 25.49),
      tx("2026-09-11", "Venmo from a friend", "Reimbursable", 51.09),
    ])).toEqual({ Reimbursable: 33.76 });
    expect(spent([tx("2026-09-11", "Tickets", "Reimbursable", -200), tx("2026-09-12", "Settled", "Reimbursable", 200)])).toEqual({});
    const over = [tx("2026-09-11", "Tickets", "Reimbursable", -200), tx("2026-09-12", "Generous", "Reimbursable", 500)];
    expect(spent(over)).toEqual({});
    expect(S.incomeIn(over, PERIOD)).toBe(0);
  });

  it("income counts only earnings", () => {
    expect(S.incomeIn([
      tx("2026-09-04", "Salary", "Income", 2105.84),
      tx("2026-09-05", "Transfer in", "Transfer", 1000),
      tx("2026-09-11", "Friend repaying", "Reimbursable", 51.09),
    ], PERIOD)).toBeCloseTo(2105.84);
  });
});

describe("allowances", () => {
  it("a monthly budget becomes a period's share", () => {
    const allowed = S.allowances(budgets({ Groceries: 250 }), PERIOD);
    expect(allowed.Groceries).toBeCloseTo((250 * 12) / 26, 4);
  });

  it("an unbudgeted category is not a zero budget", () => {
    const shopping = S.categoryLines([tx("2026-09-05", "Best Buy", "Shopping", -160.49)], budgets({ Groceries: 250 }), PERIOD)
      .find((l) => l.category === "Shopping")!;
    expect([shopping.budgeted, shopping.status, shopping.used, shopping.pace(PERIOD, TODAY)]).toEqual([false, "none", null, null]);
  });

  it("a budget with no spending still appears", () => {
    const lines = S.categoryLines([], budgets({ Groceries: 250 }), PERIOD);
    expect(lines.map((l) => l.category)).toEqual(["Groceries"]);
    expect(lines[0].left).toBeCloseTo((250 * 12) / 26, 4);
  });

  it("a period falls back to the latest month set", () => {
    expect(S.allowances(budgets({ Groceries: 250 }), P.shift(PERIOD, 2)).Groceries).toBeGreaterThan(0);
  });
});

describe("status and pace", () => {
  it.each([
    [0, "green"], [50, "green"], [92.31, "amber"], [115.38, "amber"], [115.39, "red"], [200, "red"],
  ])("spent %d -> %s", (amount, expected) => {
    expect(new S.CategoryLine("Groceries", amount, (250 * 12) / 26).status).toBe(expected);
  });

  it("pace is what is left over the days that remain, never dividing by zero", () => {
    expect(new S.CategoryLine("Groceries", 7.22, 115.38).pace(PERIOD, TODAY)).toBeCloseTo((115.38 - 7.22) / 5);
    expect(new S.CategoryLine("Dining", 58.28, 23.08).pace(PERIOD, TODAY)).toBeNull();
    const line = new S.CategoryLine("Groceries", 0, 100);
    expect(line.pace(PERIOD, d("2026-09-17"))).toBeCloseTo(100);
    expect(line.pace(PERIOD, d("2026-09-20"))).toBeCloseTo(100);
  });
});

describe("the summary", () => {
  it("takes commitments off the top", () => {
    const summary = S.summarise([tx("2026-09-04", "Salary", "Income", 2105.84), tx("2026-09-06", "Groceries", "Groceries", -40)], PERIOD, P.perPeriod(900));
    expect(summary.income).toBeCloseTo(2105.84);
    expect(summary.spent).toBeCloseTo(40);
    expect(summary.net).toBeCloseTo(2065.84);
    expect(summary.free).toBeCloseTo(2065.84 - 415.3846, 3);
  });

  it("an empty ledger is zeros, not an error", () => {
    expect(S.spentByCategory([], PERIOD)).toEqual([]);
    expect(S.incomeIn([], PERIOD)).toBe(0);
    expect(S.categoryLines([], [], PERIOD)).toEqual([]);
  });
});
