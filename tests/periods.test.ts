/**
 * The pay period, which every other figure is measured against. Consecutive
 * periods must tile the calendar exactly, and a monthly figure must convert
 * through 26 checks a year rather than "two a month".
 */

import { describe, expect, it } from "vitest";
import * as P from "../src/lib/periods";
import { d } from "./helpers";

const ANCHOR = d("2026-09-04");
const TODAY = d("2026-09-12");
const period = (when: string) => P.periodContaining(d(when), ANCHOR, "biweekly");

describe("locating a period", () => {
  it("the anchor starts its own period", () => {
    expect(period("2026-09-04").start).toBe(ANCHOR);
  });

  it("runs payday to the day before the next", () => {
    const p = period("2026-09-12");
    expect([p.start, p.end, p.days]).toEqual([d("2026-09-04"), d("2026-09-17"), 14]);
  });

  it("the day before payday belongs to the previous period", () => {
    expect(period("2026-09-03").start).toBe(d("2026-08-21"));
    expect(period("2026-09-04").start).toBe(d("2026-09-04"));
  });

  it("dates before the anchor walk backwards", () => {
    expect(period("2026-08-04").start).toBe(d("2026-07-24"));
    expect(period("2026-06-01").start).toBe(d("2026-05-29"));
  });

  it("periods tile without overlap or gap", () => {
    const p = period("2026-09-12");
    for (let step = -30; step < 30; step++) {
      const [a, b] = [P.shift(p, step), P.shift(p, step + 1)];
      expect(b.start).toBe(a.end + 1);
      expect(a.days).toBe(14);
    }
  });

  it("every day of a year lands in exactly one period", () => {
    for (let day = d("2026-01-01"); day <= d("2026-12-31"); day++) {
      const p = P.periodContaining(day, ANCHOR, "biweekly");
      expect(p.contains(day)).toBe(true);
      expect(P.shift(p, 1).contains(day)).toBe(false);
      expect(P.shift(p, -1).contains(day)).toBe(false);
    }
  });
});

describe("position inside a period", () => {
  it("counts today as spent", () => {
    const p = period("2026-09-12");
    expect(p.elapsed(d("2026-09-04"))).toBe(1);
    expect(p.elapsed(d("2026-09-12"))).toBe(9);
    expect(p.remaining(d("2026-09-12"))).toBe(5);
  });

  it("is clamped to the period", () => {
    const p = period("2026-09-12");
    expect(p.elapsed(d("2027-01-01"))).toBe(14);
    expect(p.remaining(d("2027-01-01"))).toBe(0);
  });

  it("a future period has nothing elapsed", () => {
    const p = P.shift(period("2026-09-12"), 3);
    expect(p.elapsed(TODAY)).toBe(0);
    expect(p.remaining(TODAY)).toBe(p.days);
  });

  it("labels read like a human wrote them", () => {
    expect(period("2026-09-12").label).toBe("4 – 17 Sep");
    expect(period("2026-08-25").label).toBe("21 Aug – 3 Sep");
  });
});

describe("spans", () => {
  it("a statement is sliced into the periods it spans", () => {
    const spans = P.periodsCovering(d("2026-08-04"), d("2026-09-11"), ANCHOR, "biweekly");
    expect(spans.map((s) => s.label)).toEqual(["24 Jul – 6 Aug", "7 – 20 Aug", "21 Aug – 3 Sep", "4 – 17 Sep"]);
  });

  it("a span inside one period is one period", () => {
    expect(P.periodsCovering(d("2026-09-05"), d("2026-09-09"), ANCHOR)).toHaveLength(1);
  });

  it("recent periods end with the current one", () => {
    const recent = P.recentPeriods(3, ANCHOR, "biweekly", TODAY);
    expect(recent).toHaveLength(3);
    expect(recent[2].contains(TODAY)).toBe(true);
    expect(recent[0].end).toBeLessThan(recent[1].start);
  });
});

describe("converting between a period and a month", () => {
  it("a monthly commitment costs less than half per check", () => {
    expect(P.perPeriod(900)).toBeCloseTo(415.3846, 4);
    expect(P.perPeriod(900)).toBeLessThan(450);
  });

  it("the conversions are inverses", () => {
    expect(P.perMonth(P.perPeriod(1234.56))).toBeCloseTo(1234.56, 9);
  });

  it("other cadences convert by their yearly count", () => {
    expect(P.perPeriod(900, "monthly")).toBeCloseTo(900);
    expect(P.perPeriod(900, "semimonthly")).toBeCloseTo(450);
    expect(P.perPeriod(900, "weekly")).toBeCloseTo((900 * 12) / 52);
  });
});

describe("configuration", () => {
  it.each([
    ["biweekly", "biweekly"], ["bi-weekly", "biweekly"], ["Bi Weekly", "biweekly"],
    ["fortnightly", "biweekly"], ["every 2 weeks", "biweekly"],
    ["weekly", "weekly"], ["monthly", "monthly"],
    ["semi-monthly", "semimonthly"], ["twice a month", "semimonthly"],
    ["", "biweekly"], [null, "biweekly"], ["gibberish", "biweekly"],
  ])("cadence %s -> %s", (raw, expected) => {
    expect(P.normaliseCadence(raw)).toBe(expected);
  });

  it("the anchor must be present and readable", () => {
    expect(() => P.readAnchor({})).toThrow(/not set/);
    expect(() => P.readAnchor({ pay_anchor_date: "whenever" })).toThrow(/not a date/);
  });

  it("the anchor is read from config", () => {
    expect(P.readAnchor({ pay_anchor_date: "2026-09-04" })).toBe(d("2026-09-04"));
    expect(P.readCadence({ pay_frequency: "biweekly" })).toBe("biweekly");
    expect(P.readCadence({})).toBe("biweekly");
  });
});

describe("other cadences", () => {
  it.each([
    ["semimonthly", "2026-09-01"],
    ["monthly", "2026-01-31"],
  ])("%s periods still tile", (cadence, anchor) => {
    const p = P.periodContaining(d("2026-09-12"), d(anchor), cadence);
    for (let step = -12; step < 12; step++) {
      expect(P.shift(p, step + 1).start).toBe(P.shift(p, step).end + 1);
    }
  });
});
