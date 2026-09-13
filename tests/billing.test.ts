/**
 * Learning when a subscription actually charges, from the statement —
 * including a merchant who sells other things, and a price that went up.
 */

import { describe, expect, it } from "vitest";
import * as B from "../src/lib/billing";
import * as R from "../src/lib/recurring";
import type { Recurring, Transaction } from "../src/lib/models";
import { bill, d, tx } from "./helpers";

const TODAY = d("2026-09-12");

const item = (name: string, amount: number, day: number, merchant = "", extra: Partial<Recurring> = {}) =>
  bill(name, amount, `2026-09-${String(day).padStart(2, "0")}`, { merchant, ...extra });

const charge = (date: string, description: string, amount: number) =>
  tx(date, description, "Subscriptions", -Math.abs(amount));

const observe = (items: Recurring[], charges: Transaction[]) => B.byName(B.observe(items, charges, TODAY));

it.each([
  [1, "1st"], [2, "2nd"], [3, "3rd"], [4, "4th"], [11, "11th"], [12, "12th"],
  [13, "13th"], [17, "17th"], [21, "21st"], [22, "22nd"], [23, "23rd"], [31, "31st"],
])("ordinal %i -> %s", (day, expected) => {
  expect(B.ordinal(day)).toBe(expected);
});

describe("matching", () => {
  it("a confirmed charge reports its day", () => {
    const seen = observe([item("Google One", 19.99, 17)], [charge("2026-08-17", "GOOGLE *GOOGLE ONE 855-836-3987", 19.99)])["Google One"];
    expect([seen.confirmed, seen.observedDay, seen.verdict]).toEqual([true, 17, "confirmed"]);
  });

  it("a wrong day is reported with both", () => {
    const seen = observe([item("Rent", 1875.95, 1, "pointe grand")], [
      charge("2026-08-04", "Pointe Grand Byr RENT 270105232", 1875.95),
      charge("2026-09-02", "Pointe Grand Byr RENT 272293054", 1875.95),
    ])["Rent"];
    expect(seen.verdict).toBe("wrong-day");
    expect(seen.observedDay).toBe(2);
    expect(seen.note).toContain("2nd");
    expect(seen.note).toContain("1st");
  });

  it("an unseen bill is not assumed correct", () => {
    const seen = observe([item("Water", 40, 1)], [])["Water"];
    expect([seen.confirmed, seen.verdict, seen.observedDay]).toEqual([false, "unseen", null]);
    expect(seen.note).toContain("never seen");
  });

  it("month-end clamping is not a mismatch", () => {
    expect(observe([item("Spotify", 12.99, 30, "spotify")], [charge("2026-08-31", "Spotify USA New York", 12.99)])["Spotify"].verdict).toBe("confirmed");
  });
});

const PARCELS = [
  charge("2026-08-17", "AMAZON MKTPL*2H4KL AMZN.COM/BILL WA", 14.71),
  charge("2026-08-17", "AMAZON MKTPL*9DQ3L AMZN.COM/BILL WA", 14.54),
  charge("2026-08-18", "AMAZON.COM*RT4K2 AMZN.COM/BILL WA", 16.02),
  charge("2026-08-24", "AMAZON.COM*8W2LQ AMZN.COM/BILL WA", 15.77),
  charge("2026-08-24", "AMAZON PRIME*4K2L9 AMZN.COM/BILL WA", 8.01),
  charge("2026-08-17", "AMAZON PRIME*2H4KL AMZN.COM/BILL WA", 1.06),
];

describe("a merchant who sells other things", () => {
  it("an alias picks the subscription out of the parcels, as a phrase, by the larger charge", () => {
    const seen = observe([item("Amazon", 14.99, 1, "amazon prime")], PARCELS)["Amazon"];
    expect(seen.seen).toBe(1);
    expect(seen.observedDay).toBe(24);
    expect(seen.observedAmount).toBeCloseTo(8.01);
  });

  it("without an alias the amount narrows the match", () => {
    const seen = observe([item("Amazon", 14.99, 1)], PARCELS)["Amazon"];
    expect(seen.offAmount === 0 || seen.seen <= 1).toBe(true);
  });
});

describe("a price that changed", () => {
  it("does not make a live subscription look missing, and is surfaced", () => {
    const seen = observe([item("Amazon", 14.99, 24, "amazon prime")], PARCELS)["Amazon"];
    expect(seen.verdict).toBe("confirmed");
    expect(seen.amountMatches).toBe(false);
    expect(seen.note).toContain("$8.01");
    expect(seen.note).toContain("$14.99");
  });

  it("tax does not count as a mismatch", () => {
    const seen = observe([item("Electricity", 40, 18, "flintenergies")], [charge("2026-08-18", "FlintEnergies PURCHASE 990000246", 42.55)])["Electricity"];
    expect(seen.amountMatches).toBe(true);
    expect(seen.verdict).toBe("confirmed");
  });
});

describe("housekeeping", () => {
  it("switched-off items are not checked", () => {
    expect(observe([item("Tithing", 413.04, 1, "hope church", { active: false })], [charge("2026-08-17", "HOPE CHURCH 478-", 540)])).toEqual({});
  });

  it("an empty sheet or ledger is handled", () => {
    expect(B.observe([], [], TODAY)).toEqual([]);
    expect(observe([item("Spotify", 12.99, 30)], [])["Spotify"].verdict).toBe("unseen");
  });

  it("charges outside the lookback are ignored", () => {
    expect(observe([item("Spotify", 12.99, 30, "spotify")], [charge("2025-01-31", "Spotify USA New York", 12.99)])["Spotify"].confirmed).toBe(false);
  });
});

describe("estimated, as opposed to missing", () => {
  it("an estimate is not reported as missing", () => {
    const seen = observe([item("Water", 40, 1, "", { estimated: true })], [])["Water"];
    expect(seen.verdict).toBe("estimated");
    expect(seen.note).toContain("on purpose");
    expect(seen.note).not.toContain("may not exist");
  });

  it("an estimate that starts billing becomes confirmed", () => {
    const seen = observe([item("Water", 40, 18, "flintwater", { estimated: true })], [charge("2026-08-18", "FLINTWATER UTILITY 4410", 40)])["Water"];
    expect([seen.verdict, seen.observedDay]).toEqual(["confirmed", 18]);
  });
});

describe("one charge is not a subscription", () => {
  const uppbeat = () => observe([item("Uppbeat", 8.99, 8, "uppbeat")], [charge("2026-09-08", "UPPBEAT - SUBSCRIPTION LEEDS", 8.99)])["Uppbeat"];

  it("a single charge is not proof, and asks to be confirmed", () => {
    const seen = uppbeat();
    expect(seen.confirmed).toBe(true);
    expect(seen.proven).toBe(false);
    expect(seen.note).toContain("not proof it recurs");
    expect(seen.note).toContain("confirm it if you know");
  });

  it("two charges in different months prove it; two in one month do not", () => {
    const rent = observe([item("Rent", 1875.95, 2, "pointe grand")], [
      charge("2026-08-04", "Pointe Grand Byr RENT 270105", 1875.95),
      charge("2026-09-02", "Pointe Grand Byr RENT 272293", 1875.95),
    ])["Rent"];
    expect([rent.proven, rent.monthsSeen]).toEqual([true, 2]);

    const shop = observe([item("Shop", 20, 5, "someshop")], [
      charge("2026-08-05", "SOMESHOP 001", 20), charge("2026-08-20", "SOMESHOP 002", 20),
    ])["Shop"];
    expect([shop.monthsSeen, shop.proven]).toEqual([1, false]);
  });

  it("the user saying so beats a thin statement, without inventing a charge", () => {
    const amazon = observe([item("Amazon", 14.99, 24, "amazon prime", { confirmed: true })], [charge("2026-08-24", "AMAZON PRIME*4K2 AMZN.COM/BILL", 8.01)])["Amazon"];
    expect(amazon.proven).toBe(true);
    expect(amazon.note).toContain("confirmed this recurs");

    const water = observe([item("Water", 40, 1, "", { confirmed: true, estimated: true })], [])["Water"];
    expect([water.confirmed, water.verdict]).toEqual([false, "estimated"]);
  });
});

describe("sales tax and metered bills", () => {
  const taxed = item("Amazon", 14.99, 24, "amazon prime", { taxRate: 7 });

  it("tax is added to what is budgeted, and an untaxed item is its price", () => {
    expect(R.billedAmount(taxed)).toBeCloseTo(16.04, 2);
    expect(R.billedAmount(item("Spotify", 12.99, 30))).toBeCloseTo(12.99);
  });

  it("the upcoming calendar shows the taxed figure and the listed one, and totals it", () => {
    const [due] = R.upcoming([taxed], d("2026-09-20"), 10);
    expect(due.amount).toBeCloseTo(16.04, 2);
    expect(due.listed).toBeCloseTo(14.99);
    expect(R.dueBetween([taxed], d("2026-09-20"), d("2026-09-30"))).toBeCloseTo(16.04, 2);
  });

  it("a variable bill is budgeted at the dearest charge seen; a fixed one is not overridden", () => {
    const electricity = item("Electricity", 40, 18, "flintenergies", { variable: true });
    const obs = B.observe([electricity], [
      charge("2026-07-18", "FlintEnergies PURCHASE 99000", 38.1),
      charge("2026-08-18", "FlintEnergies PURCHASE 99000", 42.55),
    ], TODAY);
    expect(B.budgetedAmounts([electricity], obs)["Electricity"]).toBeCloseTo(42.55);

    const spotify = item("Spotify", 12.99, 30, "spotify");
    expect(B.budgetedAmounts([spotify], B.observe([spotify], [charge("2026-08-30", "Spotify New York", 12.99)], TODAY))).not.toHaveProperty("Spotify");
  });

  it("a variable bill with no sightings keeps its estimate", () => {
    const water = item("Water", 40, 1, "", { variable: true });
    expect(B.budgetedAmounts([water], B.observe([water], [], TODAY))).not.toHaveProperty("Water");
  });
});
