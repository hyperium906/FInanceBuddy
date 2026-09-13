/**
 * The pay period — the unit this whole application is organised around.
 *
 * Budgeting tools almost always use the calendar month, and for someone paid
 * every two weeks that is the wrong unit. The question actually asked is never
 * "how much of September is left"; it is *"my check landed, what do I move and
 * what can I spend before the next one"*.
 *
 * So a **pay period** runs from one payday up to the day before the next, and
 * the money arriving on its first day is the money that has to last it.
 *
 * Bi-weekly means **26 paychecks a year, not 24**, which is why roughly twice
 * a year a month holds three and a per-month figure misstates the pace.
 */

import { addMonths, day, daysInMonth, floorDiv, format, mod, monthStart, parseDay, parts, type Day } from "./dates";

export type Cadence = "weekly" | "biweekly" | "semimonthly" | "monthly";

const DAY_CYCLES: Partial<Record<Cadence, number>> = { weekly: 7, biweekly: 14 };

export const PER_YEAR: Record<Cadence, number> = {
  weekly: 52, biweekly: 26, semimonthly: 24, monthly: 12,
};

/** Assumed when `_Config` names no cadence — the one whose 26/24 matters. */
export const DEFAULT_CADENCE: Cadence = "biweekly";

export const ANCHOR_KEY = "pay_anchor_date";
export const CADENCE_KEY = "pay_frequency";
export const PAYCHECK_KEY = "paycheck_amount";

export class PayScheduleError extends Error {
  name = "PayScheduleError";
}

/**
 * One stretch of time funded by a single paycheck. `start` is the payday and
 * `end` the day before the next, both inclusive, so periods tile the calendar.
 */
export class PayPeriod {
  constructor(
    readonly start: Day,
    readonly end: Day,
    readonly cadence: Cadence = DEFAULT_CADENCE,
  ) {}

  get payday(): Day {
    return this.start;
  }

  get days(): number {
    return this.end - this.start + 1;
  }

  contains(when: Day): boolean {
    return this.start <= when && when <= this.end;
  }

  /** Days already gone, today counting as spent; clamped to the period. */
  elapsed(today: Day): number {
    if (today < this.start) return 0;
    return Math.min(today - this.start + 1, this.days);
  }

  remaining(today: Day): number {
    return Math.max(this.days - this.elapsed(today), 0);
  }

  /** `4 – 17 Sep`, `21 Aug – 3 Sep`. */
  get label(): string {
    const a = parts(this.start);
    const b = parts(this.end);
    if (a.year !== b.year) return `${format(this.start, "DD Mon YYYY")} – ${format(this.end, "DD Mon YYYY")}`;
    if (a.month === b.month) return `${a.day} – ${format(this.end, "D Mon")}`;
    return `${format(this.start, "D Mon")} – ${format(this.end, "D Mon")}`;
  }
}

/** Any spelling of a pay cadence; unrecognised falls back rather than raising. */
export function normaliseCadence(raw: unknown): Cadence {
  const text = String(raw ?? "").trim().toLowerCase().replace(/[-_ ]/g, "");
  const aliases: Record<string, Cadence> = {
    weekly: "weekly", everyweek: "weekly",
    biweekly: "biweekly", fortnightly: "biweekly",
    every2weeks: "biweekly", everytwoweeks: "biweekly",
    semimonthly: "semimonthly", twicemonthly: "semimonthly",
    twiceamonth: "semimonthly", bimonthly: "semimonthly",
    monthly: "monthly", everymonth: "monthly",
  };
  return aliases[text] ?? DEFAULT_CADENCE;
}

/**
 * The anchor payday from `_Config`. Raises rather than guessing: every period
 * boundary derives from it, so a silent default would put every window out.
 */
export function readAnchor(config: Record<string, string> | null | undefined): Day {
  const raw = String(config?.[ANCHOR_KEY] ?? "").trim();
  if (!raw) {
    throw new PayScheduleError(
      `'${ANCHOR_KEY}' is not set in _Config. Put any known payday there as YYYY-MM-DD; every pay period is counted from it.`,
    );
  }
  const value = parseDay(raw);
  if (value === null) {
    throw new PayScheduleError(`'${ANCHOR_KEY}' in _Config is not a date: '${raw}'. Use YYYY-MM-DD.`);
  }
  return value;
}

export function readCadence(config: Record<string, string> | null | undefined): Cadence {
  return normaliseCadence(config?.[CADENCE_KEY]);
}

/** The payday `steps` cycles after `anchor`; `steps` may be negative. */
function advance(anchor: Day, cadence: Cadence, steps: number): Day {
  const cycle = DAY_CYCLES[cadence];
  if (cycle !== undefined) return anchor + cycle * steps;
  if (cadence === "semimonthly") {
    const { day: date } = parts(anchor);
    const low = date <= 15 ? date : date - 15;
    const position = (date <= 15 ? 0 : 1) + steps;
    const month = addMonths(monthStart(anchor), floorDiv(position, 2));
    const wanted = mod(position, 2) === 0 ? low : low + 15;
    const p = parts(month);
    return day(p.year, p.month, Math.min(wanted, daysInMonth(p.year, p.month)));
  }
  return addMonths(anchor, steps);
}

/** Whole cycles `when` sits after `anchor`: estimated, then made exact by walking. */
function indexOf(anchor: Day, cadence: Cadence, when: Day): number {
  const average = 365.25 / PER_YEAR[cadence];
  let index = Math.floor((when - anchor) / average);
  while (advance(anchor, cadence, index) > when) index -= 1;
  while (advance(anchor, cadence, index + 1) <= when) index += 1;
  return index;
}

/** The period `when` falls inside — before the anchor as well as after it. */
export function periodContaining(when: Day, anchor: Day, cadence: unknown = DEFAULT_CADENCE): PayPeriod {
  const kind = normaliseCadence(cadence);
  const index = indexOf(anchor, kind, when);
  return new PayPeriod(advance(anchor, kind, index), advance(anchor, kind, index + 1) - 1, kind);
}

export function currentPeriod(anchor: Day, cadence: unknown, today: Day): PayPeriod {
  return periodContaining(today, anchor, cadence);
}

export function shift(period: PayPeriod, steps: number): PayPeriod {
  return new PayPeriod(
    advance(period.start, period.cadence, steps),
    advance(period.start, period.cadence, steps + 1) - 1,
    period.cadence,
  );
}

/** The last `count` periods, oldest first, ending with the current one. */
export function recentPeriods(count: number, anchor: Day, cadence: unknown, today: Day): PayPeriod[] {
  const now = currentPeriod(anchor, cadence, today);
  return Array.from({ length: count }, (_, i) => shift(now, i - (count - 1)));
}

/** Every period touching `first`..`last`, oldest first. */
export function periodsCovering(first: Day, last: Day, anchor: Day, cadence: unknown = DEFAULT_CADENCE): PayPeriod[] {
  const start = periodContaining(first, anchor, cadence);
  const stop = periodContaining(last, anchor, cadence);
  const out = [start];
  while (out[out.length - 1].start < stop.start) out.push(shift(out[out.length - 1], 1));
  return out;
}

/** A monthly figure's share of one period — via the yearly count, never "half". */
export function perPeriod(monthlyAmount: number, cadence: unknown = DEFAULT_CADENCE): number {
  return (monthlyAmount * 12) / PER_YEAR[normaliseCadence(cadence)];
}

export function perMonth(periodAmount: number, cadence: unknown = DEFAULT_CADENCE): number {
  return (periodAmount * PER_YEAR[normaliseCadence(cadence)]) / 12;
}
