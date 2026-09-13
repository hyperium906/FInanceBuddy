/**
 * Recurring commitments: what they cost, and when they next bill.
 *
 * `Next Due` in the sheet goes stale the moment a bill is paid and nobody
 * edits the row, so that date is treated as an **anchor** and every occurrence
 * is computed from it: a row last touched in March still yields the right
 * September date, and a 31st bill stays on the 31st rather than walking
 * backwards through every short month.
 *
 * The cost of an item is the **magnitude** of its Amount: `-15.49` and `15.49`
 * are the same fifteen dollars leaving.
 */

import { addMonths, day, daysInMonth, floorDiv, mod, monthStart, parts, type Day } from "./dates";
import type { Recurring } from "./models";

export type Frequency = "weekly" | "biweekly" | "semimonthly" | "monthly" | "quarterly" | "semiannual" | "annual";

export const DEFAULT_FREQUENCY: Frequency = "monthly";

/** How many times each frequency lands in an average month. */
export const PER_MONTH: Record<Frequency, number> = {
  weekly: 52 / 12,
  biweekly: 26 / 12,
  semimonthly: 2,
  monthly: 1,
  quarterly: 1 / 3,
  semiannual: 1 / 6,
  annual: 1 / 12,
};

const ALIASES: Record<string, Frequency> = {
  weekly: "weekly", biweekly: "biweekly", semimonthly: "semimonthly", monthly: "monthly",
  quarterly: "quarterly", semiannual: "semiannual", annual: "annual",
  week: "weekly", everyweek: "weekly", "7days": "weekly",
  fortnightly: "biweekly", every2weeks: "biweekly",
  everytwoweeks: "biweekly", "2weeks": "biweekly", "14days": "biweekly",
  twicemonthly: "semimonthly", twiceamonth: "semimonthly",
  month: "monthly", everymonth: "monthly", permonth: "monthly",
  quarter: "quarterly", every3months: "quarterly", "3months": "quarterly",
  halfyearly: "semiannual", every6months: "semiannual", "6months": "semiannual",
  annually: "annual", yearly: "annual", year: "annual",
  everyyear: "annual", peryear: "annual", "12months": "annual",
};

const DAY_STEP: Partial<Record<Frequency, number>> = { weekly: 7, biweekly: 14 };
const MONTH_STEP: Partial<Record<Frequency, number>> = { monthly: 1, quarterly: 3, semiannual: 6, annual: 12 };
const DAYS_PER_MONTH = 365.25 / 12;

/** Ceilings so a nonsense anchor — a date in 1899 — cannot spin. */
const MAX_STEPS = 4096;
const MAX_OCCURRENCES = 512;

/** Any spelling of a frequency; unrecognised prices as monthly rather than vanishing. */
export function normaliseFrequency(raw: unknown): Frequency {
  const text = String(raw ?? "").trim().toLowerCase().replace(/[\s_\-/]+/g, " ");
  if (!text) return DEFAULT_FREQUENCY;
  return ALIASES[text.replace(/ /g, "")] ?? ALIASES[text] ?? DEFAULT_FREQUENCY;
}

export function monthlyCost(amount: number, frequency: unknown): number {
  return Math.abs(amount) * PER_MONTH[normaliseFrequency(frequency)];
}

/**
 * The `index`-th occurrence on or after `anchor`. Month-based frequencies are
 * offset from the anchor itself, so a short month's clamp is not carried on.
 */
export function occurrence(anchor: Day, frequency: Frequency, index: number): Day {
  const step = DAY_STEP[frequency];
  if (step !== undefined) return anchor + step * index;

  if (frequency === "semimonthly") {
    const { day: date } = parts(anchor);
    const low = date <= 15 ? date : date - 15;
    const position = (date <= 15 ? 0 : 1) + index;
    const start = addMonths(monthStart(anchor), floorDiv(position, 2));
    const wanted = mod(position, 2) === 0 ? low : low + 15;
    const p = parts(start);
    return day(p.year, p.month, Math.min(wanted, daysInMonth(p.year, p.month)));
  }

  return addMonths(anchor, (MONTH_STEP[frequency] ?? 1) * index);
}

/** Smallest occurrence index on or after `start`. `start` itself counts as due. */
function nextIndex(anchor: Day, frequency: Frequency, start: Day): number {
  if (anchor >= start) return 0;
  const periodDays = Math.max(DAYS_PER_MONTH / PER_MONTH[frequency], 1);
  // Undershoot deliberately, then walk.
  let index = Math.max(0, Math.trunc((start - anchor) / periodDays) - 2);
  for (let i = 0; i < MAX_STEPS; i++) {
    if (occurrence(anchor, frequency, index) >= start) return index;
    index += 1;
  }
  return index;
}

/** The next time this charge lands; a past `due` is rolled forward. */
export function nextOccurrence(due: Day | null, frequency: unknown, today: Day): Day | null {
  if (due === null) return null;
  const canonical = normaliseFrequency(frequency);
  return occurrence(due, canonical, nextIndex(due, canonical, today));
}

/** Every occurrence in `[start, end]`, in order. A weekly charge really does bill four times. */
export function occurrences(due: Day | null, frequency: unknown, start: Day, end: Day): Day[] {
  if (due === null || end < start) return [];
  const canonical = normaliseFrequency(frequency);
  let index = nextIndex(due, canonical, start);
  const out: Day[] = [];
  while (out.length < MAX_OCCURRENCES) {
    const when = occurrence(due, canonical, index);
    if (when > end) break;
    out.push(when);
    index += 1;
  }
  return out;
}

/** What actually leaves the account: the listed price plus its sales tax. */
export function billedAmount(item: Pick<Recurring, "amount" | "taxRate">): number {
  return Math.abs(item.amount || 0) * (1 + (item.taxRate || 0) / 100);
}

export interface Upcoming {
  due: Day;
  daysAway: number;
  name: string;
  category: string;
  /** Tax included. */
  amount: number;
  /** The price before tax. */
  listed: number;
  frequency: Frequency;
  accountId: string;
}

/**
 * Every charge landing between today and `horizonDays` from now — one row per
 * occurrence, not per item. Today counts: a bill dated today is not paid yet.
 */
export function upcoming(recurring: Recurring[], today: Day, horizonDays = 30, includeInactive = false): Upcoming[] {
  const end = today + Math.trunc(horizonDays);
  const rows: Upcoming[] = [];
  for (const item of recurring) {
    if (!includeInactive && !item.active) continue;
    const frequency = normaliseFrequency(item.frequency);
    for (const when of occurrences(item.nextDue, frequency, today, end)) {
      rows.push({
        due: when,
        daysAway: when - today,
        name: item.name,
        category: item.category,
        amount: billedAmount(item),
        listed: Math.abs(item.amount || 0),
        frequency,
        accountId: item.accountId,
      });
    }
  }
  return rows.sort((a, b) => a.due - b.due || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** Total billing between `start` and `end` inclusive, every occurrence counted. */
export function dueBetween(recurring: Recurring[], start: Day, end: Day, includeInactive = false): number {
  let total = 0;
  for (const item of recurring) {
    if (!includeInactive && !item.active) continue;
    total += billedAmount(item) * occurrences(item.nextDue, item.frequency, start, end).length;
  }
  return total;
}
