/**
 * Calendar days, without times or timezones.
 *
 * Everything in this app is dated to the day: a payday, a bill, a posting.
 * A `Day` is a whole number of days since 1970-01-01, so comparing two is `<`,
 * the gap between them is subtraction, and nothing can drift by an hour when
 * the server runs in UTC and the paycheck lands in Georgia.
 */

export type Day = number;

const MS_PER_DAY = 86_400_000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function day(year: number, month: number, date: number): Day {
  return Math.round(Date.UTC(year, month - 1, date) / MS_PER_DAY);
}

export function parts(value: Day): { year: number; month: number; day: number; weekday: number } {
  const stamp = new Date(value * MS_PER_DAY);
  return {
    year: stamp.getUTCFullYear(),
    month: stamp.getUTCMonth() + 1,
    day: stamp.getUTCDate(),
    weekday: stamp.getUTCDay(),
  };
}

export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Python-style floor division and modulo, which stay sensible for negatives. */
export function floorDiv(a: number, b: number): number {
  return Math.floor(a / b);
}

export function mod(a: number, b: number): number {
  return ((a % b) + b) % b;
}

/**
 * `value` moved by `months`, with the day clamped into a short month.
 *
 * 31 January plus one month is 28 February, which is what a biller does. The
 * clamp is not carried forward: callers offset from a fixed anchor rather than
 * stepping one month at a time.
 */
export function addMonths(value: Day, months: number): Day {
  const { year, month, day: date } = parts(value);
  const total = year * 12 + (month - 1) + months;
  const y = floorDiv(total, 12);
  const m = mod(total, 12) + 1;
  return day(y, m, Math.min(date, daysInMonth(y, m)));
}

export function monthStart(value: Day): Day {
  const { year, month } = parts(value);
  return day(year, month, 1);
}

export function monthEnd(value: Day): Day {
  const { year, month } = parts(value);
  return day(year, month, daysInMonth(year, month));
}

function valid(year: number, month: number, date: number): Day | null {
  if (month < 1 || month > 12 || date < 1 || date > daysInMonth(year, month)) return null;
  return day(year, month, date);
}

const MONTH_WORD = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b/i;

/**
 * Read a date out of whatever a spreadsheet cell or bank export holds.
 *
 * `2026-09-12`, `09/12/2026`, `9/12/26`, `Sep 12, 2026`, and a `Date`. Anything
 * else is null rather than a guess: a date that parses wrongly moves a bill
 * onto the wrong paycheck, silently.
 */
export function parseDay(raw: unknown): Day | null {
  if (raw === null || raw === undefined) return null;
  if (raw instanceof Date) {
    return Number.isNaN(raw.getTime())
      ? null
      : day(raw.getUTCFullYear(), raw.getUTCMonth() + 1, raw.getUTCDate());
  }
  const text = String(raw).trim();
  if (!text) return null;

  let match = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s].*)?$/.exec(text);
  if (match) return valid(+match[1], +match[2], +match[3]);

  match = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?:\s.*)?$/.exec(text);
  if (match) {
    const year = match[3].length === 2 ? 2000 + +match[3] : +match[3];
    return valid(year, +match[1], +match[2]);
  }

  // Free text only when it names a month; the engine's parser otherwise turns
  // "Q1" or "12" into a date nobody meant.
  if (!MONTH_WORD.test(text)) return null;
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) return null;
  const stamp = new Date(parsed);
  return day(stamp.getFullYear(), stamp.getMonth() + 1, stamp.getDate());
}

/** `YYYY-MM-DD`. */
export function iso(value: Day): string {
  const { year, month, day: date } = parts(value);
  return `${year}-${String(month).padStart(2, "0")}-${String(date).padStart(2, "0")}`;
}

/**
 * Format with a small token set: `YYYY`, `Month`, `Mon`, `Dow`, `DD`, `D`.
 *
 * `format(d, "D Mon")` -> `4 Sep`; `format(d, "Dow DD Mon")` -> `Fri 04 Sep`.
 */
export function format(value: Day, pattern: string): string {
  const { year, month, day: date, weekday } = parts(value);
  return pattern.replace(/YYYY|Month|Mon|Dow|DD|D/g, (token) => {
    switch (token) {
      case "YYYY": return String(year);
      case "Month": return MONTH_NAMES[month - 1];
      case "Mon": return MONTHS[month - 1];
      case "Dow": return WEEKDAYS[weekday];
      case "DD": return String(date).padStart(2, "0");
      default: return String(date);
    }
  });
}

/**
 * Today where the money is, not where the server is.
 *
 * Vercel runs in UTC, so at 9pm in Georgia the server already thinks it is
 * tomorrow — and on a Thursday evening that is payday, a period early.
 */
export function today(timeZone: string = process.env.APP_TIMEZONE || "America/New_York"): Day {
  const text = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
  return parseDay(text) as Day;
}
