/**
 * Wanting things, and whether they can be paid for. Judged one at a time,
 * thirty-seven wants become thirty-five permissions to spend.
 */

import { describe, expect, it } from "vitest";
import type { WishlistItem } from "../src/lib/models";
import * as W from "../src/lib/wishlist";
import { d } from "./helpers";

const TODAY = d("2026-09-12");
const ROOM = W.headroom(880, 42, 160, 390);

const want = (name: string, price: number, priority = "High", timeline = "Q1 2027", status = "Planned"): W.PlannerItem =>
  ({ category: "Things", name, priority, timeline, price, status, notes: "", url: "", source: "planner" });

describe("headroom", () => {
  it("subtracts what later checks need, and never goes negative", () => {
    expect(ROOM.available).toBeCloseTo(880 - 42 - 160);
    const broke = W.headroom(100, 500);
    expect([broke.available, broke.overspent]).toEqual([0, true]);
  });
});

describe("quarters", () => {
  it.each([["Q1 2027", "2027-03-31"], ["Q2 2027", "2027-06-30"], ["Q3 2027", "2027-09-30"], ["Q4 2026", "2026-12-31"]])(
    "%s ends %s", (timeline, expected) => {
      expect(W.quarterEnd(timeline)).toBe(d(expected));
    },
  );

  it.each(["", null, "soon", "Q5 2027", "Q1"])("%s is no deadline", (bad) => {
    expect(W.quarterEnd(bad)).toBeNull();
  });
});

describe("one item at a time", () => {
  it("well inside is a yes; eating almost all of it is tight", () => {
    expect(W.judge(want("Shorts", 20), ROOM, TODAY).status).toBe("yes");
    const airpods = W.judge(want("AirPods", 660), ROOM, TODAY);
    expect([airpods.affordable, airpods.status]).toEqual([true, "tight"]);
    expect(airpods.note).toContain("leaves only");
  });

  it("out of reach this check says how long; nothing is reachable without a surplus", () => {
    const piano = W.judge(want("Roli Piano", 1500), ROOM, TODAY);
    expect([piano.status, piano.periodsToAfford]).toEqual(["wait", 3]);
    expect(piano.note).toContain("3 more checks");
    const ps5 = W.judge(want("PS5", 499), W.headroom(100, 0, 0, 0), TODAY);
    expect([ps5.status, ps5.periodsToAfford]).toEqual(["out-of-reach", null]);
  });

  it("flags a deadline that cannot be met, and only that", () => {
    expect(W.judge(want("Dyson", 5000, "High", "Q4 2026"), ROOM, TODAY).missesDeadline).toBe(true);
    expect(W.judge(want("Shorts", 20, "High", "Q4 2026"), ROOM, TODAY).missesDeadline).toBe(false);
  });
});

describe("the whole list at once", () => {
  const LIST = [
    want("Cheap", 20), want("Mid", 239), want("Dear", 549), want("Dearest", 800, "Medium"),
    want("Bought", 99, "High", "Q1 2027", "Purchased"),
  ];
  const assessed = () => W.assess(LIST, ROOM, TODAY);

  it("buying one thing removes it from what is left", () => {
    expect(assessed().filter((a) => a.verdict === "yes" || a.verdict === "tight")).toHaveLength(3);
    expect(assessed().filter((a) => a.fits).map((a) => a.name)).toEqual(["Cheap", "Mid"]);
    expect(assessed().slice(0, 3).map((a) => a.running)).toEqual([20, 259, 808]);
  });

  it("a crowded-out item does not still say buy it", () => {
    const dear = assessed().find((a) => a.name === "Dear")!;
    expect(dear.fits).toBe(false);
    expect(dear.note).toContain("use the headroom first");
  });

  it("drops purchases and orders by what can be acted on, then priority", () => {
    expect(assessed().map((a) => a.name)).toEqual(["Cheap", "Mid", "Dear", "Dearest"]);
  });

  it("the summary separates individually from together", () => {
    const summary = W.summarise(assessed());
    expect([summary.items, summary.buyableNow, summary.fitsCount]).toEqual([4, 3, 2]);
    expect(summary.fitsValue).toBeCloseTo(259);
    expect(summary.total).toBeCloseTo(1608);
  });

  it("empty and fully-purchased lists are empty, not errors", () => {
    expect(W.assess([], ROOM, TODAY)).toEqual([]);
    expect(W.summarise([]).items).toBe(0);
    expect(W.assess([want("A", 10, "High", "", "Purchased")], ROOM, TODAY)).toEqual([]);
  });

  it("orders priorities", () => {
    expect(W.priorityRank("High")).toBeLessThan(W.priorityRank("Medium"));
    expect(W.priorityRank("Medium")).toBeLessThan(W.priorityRank("Low"));
    expect(W.priorityRank("")).toBe(W.priorityRank("nonsense"));
  });
});

describe("two lists read as one", () => {
  const added = (name: string, price: number, url = ""): WishlistItem => ({
    id: name.slice(0, 4), name, price, url, category: "Shopping", priority: "Medium",
    status: "Planned", addedOn: TODAY, notes: "", targetDate: null,
  });

  it("combines both tabs, either side may be empty", () => {
    const combined = W.combine([want("Guitar", 450)], [added("Monitor", 300)]);
    expect(combined.map((c) => [c.name, c.source])).toEqual([["Guitar", "planner"], ["Monitor", "added here"]]);
    expect(W.combine([], [])).toEqual([]);
  });

  it("a link survives, and added items compete for the same headroom", () => {
    const [first] = W.assess(W.combine([], [added("Monitor", 300, "https://example.com/m")]), ROOM, TODAY);
    expect([first.url, first.source]).toEqual(["https://example.com/m", "added here"]);
    const fits = W.assess(W.combine([want("Cheap", 20), want("Mid", 239)], [added("Monitor", 600)]), ROOM, TODAY).filter((a) => a.fits);
    expect(fits.reduce((s, a) => s + a.price, 0)).toBeLessThanOrEqual(ROOM.available);
  });
});
