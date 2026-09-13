/**
 * Learning when a subscription actually charges, from the statement.
 *
 * `_Recurring` holds a day somebody typed in, and a typed day is a guess that
 * ages badly. Since the day decides which paycheck a bill falls under, a wrong
 * one puts the charge on the wrong check. The statement already knows: this
 * matches each charge back to its row and reports what was observed.
 *
 * A row with no matching charge is **unconfirmed** rather than assumed right.
 * "We have never seen this bill" and "this bill is due on the 1st" are
 * different claims.
 */

import { parts, type Day } from "./dates";
import { scrubMerchant } from "./categorize";
import { dollars } from "./money";
import type { Recurring, Transaction } from "./models";

/** Words carrying no identity. "Software / Music" must not match on "music". */
const NOISE = new Set([
  "the", "and", "inc", "llc", "ltd", "co", "com", "one", "plus", "pro",
  "premium", "subscription", "monthly", "annual", "service", "services",
  "software", "music", "payment", "bill", "billing", "auto",
]);

/** Four months catches a quarterly bill once and a monthly one three or four times. */
export const LOOKBACK_DAYS = 130;

/**
 * Only to narrow a match when no merchant alias is given. Tax makes a charge
 * larger than its list price, and prices change, so it is never a gate.
 */
export const AMOUNT_TOLERANCE = 0.35;

export interface Charge {
  when: Day;
  amount: number;
}

/**
 * The largest charge in each calendar month. A subscription bills once a
 * cycle, and beside an incidental charge from the same merchant the renewal
 * is the larger — which is how Prime's $8.01 beats a $1.06 rental.
 */
function onePerMonth(charges: Charge[]): Charge[] {
  const byMonth = new Map<string, Charge>();
  for (const charge of charges) {
    const { year, month } = parts(charge.when);
    const key = `${year}-${month}`;
    const held = byMonth.get(key);
    if (!held || charge.amount > held.amount) byMonth.set(key, charge);
  }
  return [...byMonth.values()].sort((a, b) => a.when - b.when || a.amount - b.amount);
}

/** `1` -> `1st`. Because "the 31th" makes a page look broken. */
export function ordinal(day: number): string {
  if (day % 100 >= 10 && day % 100 <= 20) return `${day}th`;
  return `${day}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[day % 10] ?? "th"}`;
}

function needles(name: string): string[] {
  const tokens = String(name ?? "").toLowerCase().replace(/[/-]/g, " ").split(/\s+/).filter(Boolean);
  const keep = tokens.filter((token) => !NOISE.has(token) && token.length > 2);
  return (keep.length ? keep : tokens).sort((a, b) => b.length - a.length);
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export class Observation {
  constructor(
    readonly name: string,
    readonly sheetDay: number | null,
    readonly sheetAmount: number,
    readonly estimated = false,
    /** You have said this recurs. Beats the sighting count on a short statement. */
    readonly asserted = false,
    readonly charges: Charge[] = [],
    /** Charges from the merchant that do not match the stated amount. */
    readonly offAmount = 0,
  ) {}

  get seen(): number {
    return this.charges.length;
  }

  get confirmed(): boolean {
    return this.seen > 0;
  }

  private get latest(): Charge | null {
    let best: Charge | null = null;
    for (const charge of this.charges) if (!best || charge.when > best.when) best = charge;
    return best;
  }

  /** The day it charges, from the most recent sighting — so a moved date is followed. */
  get observedDay(): number | null {
    return this.latest ? parts(this.latest.when).day : null;
  }

  get observedAmount(): number | null {
    return this.latest ? this.latest.amount : null;
  }

  get monthsSeen(): number {
    return new Set(this.charges.map((c) => { const p = parts(c.when); return `${p.year}-${p.month}`; })).size;
  }

  /**
   * Whether the statement demonstrates this recurs. One charge is a purchase;
   * two in different months is a subscription. Both rows once inferred from a
   * single sighting turned out to be one-off payments.
   */
  get proven(): boolean {
    return this.asserted || this.monthsSeen >= 2;
  }

  get lastSeen(): Day | null {
    return this.latest?.when ?? null;
  }

  get dayMatches(): boolean {
    if (this.observedDay === null || this.sheetDay === null) return false;
    if (this.observedDay === this.sheetDay) return true;
    // A 31st bill shows as the 30th in a short month: the calendar, not the biller.
    return this.observedDay >= 28 && this.sheetDay >= 28;
  }

  get amountMatches(): boolean {
    const observed = this.observedAmount;
    if (observed === null || !this.sheetAmount) return false;
    return Math.abs(observed - this.sheetAmount) <= this.sheetAmount * AMOUNT_TOLERANCE;
  }

  /** `confirmed` / `wrong-day` / `estimated` / `unseen`. */
  get verdict(): "confirmed" | "wrong-day" | "estimated" | "unseen" {
    if (!this.confirmed) return this.estimated ? "estimated" : "unseen";
    return this.dayMatches ? "confirmed" : "wrong-day";
  }

  /** A sentence a person can act on. */
  get note(): string {
    const times = this.seen !== 1 ? `${this.seen}×` : "once";
    if (this.verdict === "estimated") {
      return (
        "an estimate you entered on purpose — not billed yet, so the " +
        `${this.sheetDay ? ordinal(this.sheetDay) : "day"} is a placeholder until the first charge appears`
      );
    }
    if (this.verdict === "unseen") {
      if (this.offAmount) {
        return (
          `${this.offAmount} charge(s) from this merchant, but none near ${dollars(this.sheetAmount)} — ` +
          "the amount may be wrong, or this may not be a subscription at all"
        );
      }
      return (
        "never seen in the statement — the day is assumed, so this may be on the wrong check, " +
        "may be billed under another name, or may not exist any more"
      );
    }
    let drift = "";
    if (!this.amountMatches && this.observedAmount !== null) {
      const direction = this.sheetAmount > this.observedAmount ? "up from" : "down from";
      drift = `; last charged ${dollars(this.observedAmount)}, so the sheet's ${dollars(this.sheetAmount)} is ${direction} what you have paid`;
    }
    const observed = ordinal(this.observedDay as number);
    if (this.verdict === "wrong-day") {
      return `charged ${times}, on the ${observed} — the sheet says the ${ordinal(this.sheetDay as number)}${drift}`;
    }
    if (!this.proven) {
      return `seen once, on the ${observed}${drift} — one charge is not proof it recurs; confirm it if you know it does`;
    }
    if (this.asserted && this.monthsSeen < 2) {
      return `you have confirmed this recurs; charged on the ${observed}${drift}`;
    }
    return `charged ${times}, on the ${observed}${drift || " — matches"}`;
  }
}

/**
 * Match every active recurring item to its charges in the statement, on the
 * scrubbed merchant name, so card numbers and reference ids cannot block it.
 */
export function observe(
  recurring: Recurring[],
  transactions: Transaction[],
  today: Day,
  lookbackDays = LOOKBACK_DAYS,
): Observation[] {
  const recent = transactions
    .filter((t) => t.date !== null && t.date >= today - lookbackDays)
    .map((t) => ({ merchant: scrubMerchant(t.description), when: t.date as Day, amount: Math.abs(t.amount || 0) }));

  const out: Observation[] = [];
  for (const row of recurring) {
    const name = String(row.name ?? "").trim();
    if (!name || !row.active) continue;
    const sheetAmount = Math.abs(row.amount || 0);
    const sheetDay = row.nextDue === null ? null : parts(row.nextDue).day;

    const alias = String(row.merchant ?? "").trim().toLowerCase();
    let hit: (merchant: string) => boolean;
    if (alias) {
      // An alias is a phrase, matched literally. Split into tokens, "amazon
      // prime" would match every parcel — the ambiguity it was written to end.
      hit = (merchant) => merchant.includes(alias);
    } else {
      const patterns = needles(name).map((needle) => new RegExp(`\\b${escapeRegExp(needle)}\\b`));
      hit = (merchant) => patterns.some((pattern) => pattern.test(merchant));
    }
    const found: Charge[] = recent.filter((t) => hit(t.merchant)).map(({ when, amount }) => ({ when, amount }));

    // An alias is trusted outright. Without one the name match is fuzzy, so the
    // amount narrows it — accepting that a price change looks like a
    // disappearance until an alias is set.
    let near = found;
    let off = 0;
    if (!alias && sheetAmount) {
      near = found.filter((c) => Math.abs(c.amount - sheetAmount) <= sheetAmount * AMOUNT_TOLERANCE);
      off = found.length - near.length;
    }

    out.push(new Observation(name, sheetDay, sheetAmount, row.estimated, row.confirmed, onePerMonth(near), off));
  }
  return out;
}

/**
 * What to reserve for each **variable** bill: the dearest charge actually
 * seen, because the typed figure on a metered bill is a guess and reserving
 * money is about not coming up short in a hot month. Fixed-price bills keep
 * their listed amount plus tax, so they are absent here.
 */
export function budgetedAmounts(recurring: Recurring[], observations: Observation[]): Record<string, number> {
  const seen = byName(observations);
  const out: Record<string, number> = {};
  for (const row of recurring) {
    const name = String(row.name ?? "").trim();
    if (!name || !row.variable) continue;
    const charges = seen[name]?.charges ?? [];
    if (charges.length) out[name] = Math.max(...charges.map((c) => c.amount));
  }
  return out;
}

export function byName(observations: Observation[]): Record<string, Observation> {
  return Object.fromEntries(observations.map((o) => [o.name, o]));
}
