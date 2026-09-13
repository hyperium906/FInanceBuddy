/** Bank-feed rows: dated where the money is, identified for the next sync, and never silently dropped. */

import { describe, expect, it } from "vitest";
import * as F from "../src/lib/simplefin";
import { d } from "./helpers";

const TZ = "America/New_York";
// 2026-09-12 01:30 UTC is still the 11th in Georgia.
const LATE_ON_THE_11TH = Date.UTC(2026, 8, 12, 1, 30) / 1000;

const account: F.SfinAccount = {
  id: "ACT-1", name: "Total Checking", conn_id: "CON-1", currency: "USD",
  balance: "2080.59", "balance-date": LATE_ON_THE_11TH,
  transactions: [
    { id: "t2", posted: LATE_ON_THE_11TH, amount: "-110.34", description: "ALDI 3003" },
    { id: "t1", posted: Date.UTC(2026, 8, 10, 15) / 1000, amount: "-4.27", description: "COOK OUT" },
    { id: "t3", posted: 0, amount: "-9.99", description: "PENDING THING", pending: true },
  ],
};

describe("turning a feed into ledger rows", () => {
  it("dates each posting in the app's timezone, oldest first, skipping pending", () => {
    const rows = F.toLedgerRows(account, "chase_checking", TZ);
    expect(rows.map((r) => [r.date, r.amount, r.accountId])).toEqual([
      [d("2026-09-10"), -4.27, "chase_checking"],
      [d("2026-09-11"), -110.34, "chase_checking"],
    ]);
  });

  it("keeps the sync id in Notes, readable back out", () => {
    const [row] = F.toLedgerRows(account, "chase_checking", TZ);
    expect(F.syncIdIn(row.notes)).toBe("ACT-1:t1");
    expect(F.syncIdIn("Logged by hand")).toBeNull();
  });

  it("reads balances and names the institution from either protocol version", () => {
    const v2 = F.bankAccounts({ accounts: [account], connections: [{ conn_id: "CON-1", name: "Chase" }] }, TZ);
    expect(v2[0]).toMatchObject({ id: "ACT-1", institution: "Chase", balance: 2080.59, balanceDate: d("2026-09-11") });
    const v1 = F.bankAccounts({ accounts: [{ ...account, conn_id: undefined, org: { name: "Chase Bank" } }] }, TZ);
    expect(v1[0].institution).toBe("Chase Bank");
  });

  it("collects and cleans error messages from both error shapes", () => {
    expect(F.errorMessages({ errlist: [{ code: "con.auth", msg: "Log in again" }], errors: ["Old style"] })).toEqual(["Log in again", "Old style"]);
  });
});

describe("rows the sheet may already hold", () => {
  const row = (date: string, amount: number, accountId = "chase_checking") => ({ date: d(date), amount, accountId });

  it("flags the same amount on the same account within the window", () => {
    expect(F.flagNearDuplicates([row("2026-09-11", -110.34)], [row("2026-09-10", -110.34)])).toEqual([true]);
  });

  it("does not flag a different account, amount, or a date outside the window", () => {
    const held = [row("2026-09-10", -110.34)];
    expect(F.flagNearDuplicates([row("2026-09-11", -110.34, "chase_savings")], held)).toEqual([false]);
    expect(F.flagNearDuplicates([row("2026-09-11", -110.35)], held)).toEqual([false]);
    expect(F.flagNearDuplicates([row("2026-09-20", -110.34)], held)).toEqual([false]);
  });

  it("one held row vouches for only one incoming row", () => {
    expect(F.flagNearDuplicates([row("2026-09-11", -4.27), row("2026-09-12", -4.27)], [row("2026-09-11", -4.27)])).toEqual([true, false]);
  });
});

describe("matching a sheet account to a bank account", () => {
  const bank = [
    { id: "a", name: "Total Checking", institution: "Chase" },
    { id: "b", name: "Chase Savings", institution: "Chase" },
    { id: "c", name: "Roth IRA", institution: "Fidelity" },
  ];

  it("picks the account sharing the most words, or none", () => {
    expect(F.guessBankAccount({ id: "chase_savings", name: "Chase Savings", institution: "Chase", type: "savings" }, bank)).toBe("b");
    expect(F.guessBankAccount({ id: "chase_checking", name: "Checking", institution: "Chase", type: "checking" }, bank)).toBe("a");
    expect(F.guessBankAccount({ id: "alpaca", name: "Alpaca", institution: "", type: "investment" }, bank)).toBe("");
  });
});
