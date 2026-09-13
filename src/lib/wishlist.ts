/**
 * Wanting things, and whether they can be paid for yet.
 *
 * The question is not "is there enough in the account" — there usually is,
 * right up to the day rent leaves. It is *"can this be bought without breaking
 * something later"*.
 *
 * **Headroom** makes the difference: what is left of this check after bills
 * and transfers, minus what is already spent from it, minus what the next rent
 * check needs carried into it. That money is sitting in the account mid-month
 * and it is already spoken for.
 */

import { day, daysInMonth, parseDay, type Day } from "./dates";
import type { WishlistItem } from "./models";
import { dollars, sum, toNumber } from "./money";

/** Leaving less than this share, or less than the floor, makes a purchase "tight". */
export const TIGHT_SHARE = 0.25;
export const TIGHT_FLOOR = 150;

/** Beyond this many checks of saving, "wait" stops being useful advice. */
export const REACH_PERIODS = 26;

export const PRIORITY_ORDER = ["high", "medium", "low"];

export interface PlannerItem {
  category: string;
  name: string;
  priority: string;
  timeline: string;
  price: number;
  status: string;
  notes: string;
  url: string;
  source: "planner" | "added here";
}

/** The hand-kept planner and items added in the app's `_Wishlist`, read as one list. */
export function combine(planner: PlannerItem[], added: WishlistItem[]): PlannerItem[] {
  return [
    ...planner.map((row) => ({ ...row, url: row.url ?? "", source: "planner" as const })),
    ...added.map((row) => ({
      category: row.category,
      name: row.name,
      priority: row.priority,
      timeline: row.targetDate === null ? "" : String(row.targetDate),
      price: row.price || 0,
      status: row.status || "Planned",
      notes: row.notes,
      url: row.url,
      source: "added here" as const,
    })),
  ];
}

/** `Q1 2027` -> the last day of that quarter. "By Q1" means by the end of March. */
export function quarterEnd(timeline: unknown): Day | null {
  const text = String(timeline ?? "").trim().toUpperCase();
  const pieces = text.split(/\s+/);
  if (!text.startsWith("Q") || pieces.length !== 2) {
    // Added items carry their target date as a day number.
    if (/^\d{5}$/.test(text)) return Number(text);
    return parseDay(timeline);
  }
  const [quarter, year] = pieces;
  if (!/^\d+$/.test(quarter.slice(1)) || !/^\d+$/.test(year)) return null;
  const number = Number(quarter.slice(1));
  if (number < 1 || number > 4) return null;
  const y = Number(year);
  return day(y, number * 3, daysInMonth(y, number * 3));
}

export function priorityRank(priority: unknown): number {
  const index = PRIORITY_ORDER.indexOf(String(priority ?? "").trim().toLowerCase());
  return index >= 0 ? index : PRIORITY_ORDER.length;
}

export class Headroom {
  constructor(
    readonly leftOfCheck: number,
    readonly alreadySpent = 0,
    /** The part of this check a later one already needs. Not yours to spend. */
    readonly reserved = 0,
    readonly surplusPerPeriod = 0,
  ) {}

  get available(): number {
    return Math.max(this.leftOfCheck - this.alreadySpent - this.reserved, 0);
  }

  get overspent(): boolean {
    return this.leftOfCheck - this.alreadySpent - this.reserved < 0;
  }
}

export function headroom(leftOfCheck: number, alreadySpent = 0, reserved = 0, surplusPerPeriod = 0): Headroom {
  return new Headroom(leftOfCheck, alreadySpent, reserved, surplusPerPeriod);
}

export type VerdictStatus = "yes" | "tight" | "wait" | "out-of-reach";

export class Verdict {
  constructor(
    readonly name: string,
    readonly price: number,
    readonly available: number,
    readonly periodsToAfford: number | null,
    readonly deadline: Day | null = null,
    readonly today: Day = 0,
  ) {}

  get affordable(): boolean {
    return this.price <= this.available;
  }

  get leaves(): number {
    return this.available - this.price;
  }

  get tight(): boolean {
    if (!this.affordable || this.available <= 0) return false;
    return this.leaves < Math.max(this.available * TIGHT_SHARE, TIGHT_FLOOR);
  }

  get status(): VerdictStatus {
    if (this.affordable) return this.tight ? "tight" : "yes";
    if (this.periodsToAfford === null || this.periodsToAfford > REACH_PERIODS) return "out-of-reach";
    return "wait";
  }

  /** Saving for it would run past the date it was wanted by. */
  get missesDeadline(): boolean {
    if (this.deadline === null || this.affordable || this.periodsToAfford === null) return false;
    return this.today + this.periodsToAfford * 14 > this.deadline;
  }

  get note(): string {
    if (this.status === "yes") return `buy it — leaves ${dollars(this.leaves)} of headroom`;
    if (this.status === "tight") return `affordable, but leaves only ${dollars(this.leaves)} behind`;
    if (this.status === "out-of-reach") return "not reachable on the current surplus";
    const checks = this.periodsToAfford as number;
    return `${checks} more check${checks !== 1 ? "s" : ""} of saving`;
  }
}

function periodsToAfford(price: number, available: number, surplus: number): number | null {
  const gap = price - available;
  if (gap <= 0) return 0;
  if (surplus <= 0) return null;
  return Math.ceil(gap / surplus);
}

export function judge(item: Pick<PlannerItem, "name" | "price" | "timeline">, room: Headroom, today: Day = 0): Verdict {
  const price = toNumber(item.price);
  return new Verdict(
    String(item.name ?? ""),
    price,
    room.available,
    periodsToAfford(price, room.available, room.surplusPerPeriod),
    quarterEnd(item.timeline),
    today,
  );
}

export interface Assessed {
  name: string;
  category: string;
  priority: string;
  timeline: string;
  price: number;
  status: string;
  verdict: VerdictStatus;
  leaves: number;
  checks: number | null;
  deadline: Day | null;
  misses: boolean;
  note: string;
  running: number;
  fits: boolean;
  url: string;
  source: string;
}

const ORDER: Record<VerdictStatus, number> = { yes: 0, tight: 1, wait: 2, "out-of-reach": 3 };

/**
 * Every wanted item with a verdict, in the order it can be acted on, then by
 * priority, then price.
 *
 * Judged one at a time, thirty-seven wants become thirty-five permissions to
 * spend. Walking the list in order and stopping when the headroom runs out is
 * the version that can be acted on.
 */
export function assess(planner: Array<Partial<PlannerItem>>, room: Headroom, today: Day = 0): Assessed[] {
  const wanted = planner.filter((item) => String(item.status ?? "Planned").trim().toLowerCase() !== "purchased");

  const rows = wanted.map((item) => {
    const verdict = judge({ name: item.name ?? "", price: item.price ?? 0, timeline: item.timeline ?? "" }, room, today);
    return {
      row: {
        name: verdict.name,
        category: String(item.category || "Uncategorised"),
        priority: String(item.priority ?? ""),
        timeline: String(item.timeline ?? ""),
        price: verdict.price,
        status: String(item.status || "Planned"),
        verdict: verdict.status,
        leaves: verdict.leaves,
        checks: verdict.periodsToAfford,
        deadline: verdict.deadline,
        misses: verdict.missesDeadline,
        note: verdict.note,
        running: 0,
        fits: false,
        url: String(item.url ?? ""),
        source: String(item.source || "planner"),
      } satisfies Assessed,
      rank: [ORDER[verdict.status], priorityRank(item.priority), verdict.price],
    };
  });

  rows.sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1] || a.rank[2] - b.rank[2]);

  let running = 0;
  return rows.map(({ row }) => {
    running += row.price;
    const fits = running <= room.available;
    // An item that fits alone but sits below where the headroom ran out must
    // not still read "buy it": the items above it have spoken for the money.
    const crowded = !fits && (row.verdict === "yes" || row.verdict === "tight");
    return {
      ...row,
      running,
      fits,
      note: crowded ? "affordable on its own — the items above it use the headroom first" : row.note,
    };
  });
}

export interface WishlistSummary {
  items: number;
  total: number;
  /** Individually within headroom. */
  buyableNow: number;
  buyableValue: number;
  missingDeadline: number;
  /** How many can be taken together. */
  fitsCount: number;
  fitsValue: number;
  average: number;
}

export function summarise(assessed: Assessed[]): WishlistSummary {
  const buyable = assessed.filter((a) => a.verdict === "yes" || a.verdict === "tight");
  const fits = assessed.filter((a) => a.fits);
  const total = sum(assessed.map((a) => a.price));
  return {
    items: assessed.length,
    total,
    buyableNow: buyable.length,
    buyableValue: sum(buyable.map((a) => a.price)),
    missingDeadline: assessed.filter((a) => a.misses).length,
    fitsCount: fits.length,
    fitsValue: sum(fits.map((a) => a.price)),
    average: assessed.length ? total / assessed.length : 0,
  };
}
