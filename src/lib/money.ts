/**
 * Money primitives shared by every other module.
 *
 * Nothing here knows about pay periods or budgets. It exists so that reading a
 * currency-formatted cell behaves the same way everywhere, rather than each
 * caller inventing its own coercion and disagreeing at the third decimal.
 *
 * Sign convention, applied throughout: transaction amounts are signed the way
 * a bank writes them — **negative is money out, positive is money in**.
 */

/** Cell contents that mean "no value" rather than a number. None are zero. */
const BLANKS = new Set(["", "-", "--", "#N/A", "N/A", "#REF!", "#VALUE!", "None", "nan"]);

/**
 * Parse a possibly currency-formatted value into a number.
 *
 * Accepts `"$1,875.95"`, `"(250.00)"` for accounting negatives, `"5.25%"`, and
 * plain numbers. Returns null for blanks and anything unusable — never 0,
 * because a blank cell and a zero are different claims.
 */
export function parseMoney(raw: unknown): number | null {
  if (raw === null || raw === undefined || typeof raw === "boolean") return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;

  const text = String(raw).trim();
  if (BLANKS.has(text)) return null;
  const negative = text.startsWith("(") && text.endsWith(")");
  const cleaned = text.replace(/[^\d.\-]/g, "");
  if (cleaned === "" || cleaned === "-" || cleaned === ".") return null;
  const value = Number(cleaned);
  if (!Number.isFinite(value)) return null;
  return negative ? -value : value;
}

/** A number out of anything, or 0 when there is none to be had. */
export function toNumber(raw: unknown): number {
  return parseMoney(raw) ?? 0;
}

function grouped(value: number): string {
  return Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Currency with negatives in parentheses: `$1,875.95`, `($250.00)`, `—`. */
export function formatCurrency(value: number | null | undefined, symbol = "$"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const rendered = `${symbol}${grouped(value)}`;
  return value < 0 ? `(${rendered})` : rendered;
}

/** A plain dollar figure for prose, where parentheses would read oddly: `$14.99`. */
export function dollars(value: number): string {
  return `${value < 0 ? "-" : ""}$${grouped(value)}`;
}

export function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Lower-cased, trimmed text, for comparisons. */
export function lower(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

/**
 * Display-cased text with an empty value named rather than blank.
 *
 * A blank category rendered as an empty string reads as a broken chart label;
 * naming it says what it is.
 */
export function labelOf(value: unknown, blank = "Uncategorized"): string {
  const text = String(value ?? "").trim();
  return text || blank;
}

export function percent(share: number, digits = 0): string {
  return `${(share * 100).toFixed(digits)}%`;
}

export function sum(values: Iterable<number>): number {
  let total = 0;
  for (const value of values) total += value;
  return total;
}
