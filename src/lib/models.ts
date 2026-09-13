/**
 * The sheet schema, and the typed rows read out of it.
 *
 * Each underscore-prefixed data tab is described once here: its header row,
 * how each value is coerced when read, and how it is rendered when written.
 * Change a header here and the reader, the writers, and the column resolution
 * all follow.
 *
 * The workbook is hand-maintained, so a tab may spell a column differently
 * (`account_id` for `Account ID`) or store a fact in another shape (a
 * `due_day` instead of a `Next Due` date). Aliases cover renames; the adapter
 * in `readTable` covers the shapes. Nothing is ever renamed in the sheet.
 */

import { addMonths, day, daysInMonth, parseDay, parts, type Day } from "./dates";
import { parseMoney } from "./money";

export type Kind = "text" | "money" | "number" | "percent" | "int" | "bool" | "date";

export interface Column {
  /** Canonical header — what a tab this app creates is given. */
  header: string;
  /** Property name on the typed row. */
  key: string;
  kind: Kind;
  /** Other spellings the same column may carry in the workbook. */
  aliases?: string[];
  /** A column the app can do without; its absence is not a problem. */
  optional?: boolean;
}

const col = (header: string, key: string, kind: Kind = "text", aliases: string[] = [], optional = false): Column =>
  ({ header, key, kind, aliases, optional });

export const TAB = {
  accounts: "_Accounts",
  transactions: "_Transactions",
  recurring: "_Recurring",
  budgets: "_Budgets",
  allocations: "_Allocations",
  goals: "_Goals",
  wishlist: "_Wishlist",
  config: "_Config",
  rules: "_Rules",
  planner: "Wishlist & Purchases",
  report: "Budget Sheet",
} as const;

export interface Account {
  id: string; name: string; type: string; institution: string;
  balance: number; currency: string; lastUpdated: Day | null;
}

export interface Transaction {
  id: string; date: Day | null; accountId: string; description: string;
  category: string; amount: number; notes: string;
}

export interface Recurring {
  id: string; name: string; category: string; amount: number; frequency: string;
  nextDue: Day | null; accountId: string; active: boolean;
  /** How the charge appears on the statement, when that differs from its name. */
  merchant: string;
  /** A placeholder figure entered deliberately, for something not billed yet. */
  estimated: boolean;
  /** You saying this recurs — beats thin statement evidence. */
  confirmed: boolean;
  /** Sales tax added at the till, in percent points. Per item: it varies by merchant. */
  taxRate: number;
  /** Amount moves with usage; the dearest charge seen is the better budget. */
  variable: boolean;
}

export interface Budget { id: string; month: string; category: string; amount: number; notes: string }

export interface Allocation {
  id: string;
  /** Blank means a standing paycheck rule; a month means recorded history. */
  month: string;
  bucket: string; percent: number; amount: number; accountId: string; notes: string;
}

export interface WishlistItem {
  id: string; name: string; price: number; url: string; category: string;
  priority: string; status: string; addedOn: Day | null; notes: string; targetDate: Day | null;
}

export interface GoalRow {
  id: string; name: string; targetAmount: number; savedAmount: number;
  targetDate: Day | null; bucket: string; accountId: string; notes: string;
}

export interface RuleRow { match: string; category: string }

export const SCHEMA: Record<string, Column[]> = {
  [TAB.accounts]: [
    col("Account ID", "id", "text", ["account_id"]),
    col("Name", "name", "text", ["name"]),
    col("Type", "type", "text", ["type"]),
    col("Institution", "institution", "text", ["institution"]),
    col("Balance", "balance", "money", ["balance"]),
    col("Currency", "currency", "text", ["currency"], true),
    col("Last Updated", "lastUpdated", "date", ["last_updated"]),
  ],
  [TAB.transactions]: [
    col("Transaction ID", "id", "text", ["txn_id", "transaction_id"]),
    col("Date", "date", "date", ["date"]),
    col("Account ID", "accountId", "text", ["account_id"]),
    col("Description", "description", "text", ["description"]),
    col("Category", "category", "text", ["category"]),
    col("Amount", "amount", "money", ["amount"]),
    col("Notes", "notes", "text", ["notes"], true),
  ],
  [TAB.recurring]: [
    col("Recurring ID", "id", "text", ["recurring_id"], true),
    col("Name", "name", "text", ["name"]),
    col("Category", "category", "text", ["category"]),
    col("Amount", "amount", "money", ["amount"]),
    col("Frequency", "frequency", "text", ["frequency"]),
    col("Next Due", "nextDue", "date", ["next_due"]),
    col("Account ID", "accountId", "text", ["account_id"], true),
    col("Active", "active", "bool", ["active"]),
    col("Merchant", "merchant", "text", ["merchant"], true),
    col("Estimated", "estimated", "bool", ["estimated"], true),
    col("Confirmed", "confirmed", "bool", ["confirmed"], true),
    col("Tax Rate", "taxRate", "percent", ["tax_rate"], true),
    col("Variable", "variable", "bool", ["variable"], true),
  ],
  [TAB.budgets]: [
    col("Budget ID", "id", "text", ["budget_id"], true),
    col("Month", "month", "text", ["month"]),
    col("Category", "category", "text", ["category"]),
    col("Amount", "amount", "money", ["planned_amount", "amount"]),
    col("Notes", "notes", "text", ["notes"], true),
  ],
  [TAB.allocations]: [
    col("Allocation ID", "id", "text", ["allocation_id"], true),
    col("Month", "month", "text", ["month"], true),
    col("Bucket", "bucket", "text", ["bucket"]),
    col("Percent", "percent", "percent", ["percent"]),
    col("Amount", "amount", "money", ["amount"]),
    col("Account ID", "accountId", "text", ["target_account", "account_id"]),
    col("Notes", "notes", "text", ["notes"], true),
  ],
  [TAB.wishlist]: [
    col("Item ID", "id"),
    col("Name", "name"),
    col("Price", "price", "money"),
    col("URL", "url"),
    col("Category", "category"),
    col("Priority", "priority"),
    col("Status", "status"),
    col("Added On", "addedOn", "date"),
    col("Notes", "notes", "text", [], true),
    // Trailing on purpose: an appended column keeps an older sheet lined up.
    col("Target Date", "targetDate", "date"),
  ],
  [TAB.goals]: [
    col("Goal ID", "id"),
    col("Name", "name"),
    col("Target Amount", "targetAmount", "money"),
    col("Saved Amount", "savedAmount", "money"),
    col("Target Date", "targetDate", "date"),
    col("Bucket", "bucket"),
    col("Account ID", "accountId"),
    col("Notes", "notes", "text", [], true),
  ],
  [TAB.config]: [
    col("Key", "key", "text", ["key"]),
    col("Value", "value", "text", ["value"]),
  ],
  // Learned merchant rules. The shipped starter rules live in the code; this
  // tab holds only what was learned from corrections, so it can be read,
  // edited, or cleared by hand like the rest of the workbook.
  [TAB.rules]: [
    col("Match", "match", "text", ["match"]),
    col("Category", "category", "text", ["category"]),
  ],
};

/** `_Config` keys the app reads, and the other names they may appear under. */
export const CONFIG_KEY_ALIASES: Record<string, string[]> = {
  paycheck_amount: ["paycheck_net", "paycheck_take_home"],
  pay_anchor_date: ["pay_anchor", "anchor_date"],
  monthly_savings_target: ["savings_target"],
  rollover_enabled: ["rollover"],
};

/** Foreign layouts: a day-of-month instead of `Next Due`, and a typed value column. */
export const DUE_DAY_COLUMN = "due_day";
export const ALLOCATION_TYPE_COLUMN = "allocation_type";
export const ALLOCATION_VALUE_COLUMN = "value";

// --------------------------------------------------------------------------
// Coercion
// --------------------------------------------------------------------------

export function toBool(raw: unknown): boolean {
  if (typeof raw === "boolean") return raw;
  return ["true", "yes", "y", "1", "x", "✓"].includes(String(raw ?? "").trim().toLowerCase());
}

export function coerce(raw: unknown, kind: Kind): unknown {
  switch (kind) {
    case "date": return parseDay(raw);
    case "money":
    case "number":
    case "percent": return parseMoney(raw) ?? 0;
    case "int": {
      const value = parseMoney(raw);
      return value === null ? 0 : Math.trunc(value);
    }
    case "bool": return toBool(raw);
    default: return String(raw ?? "").trim();
  }
}

/**
 * Map each canonical header to its position in `actual`.
 *
 * Canonical name first, then aliases. Positions come from the row handed in —
 * never schema order — so a reordered or extended tab reads the right cells.
 */
export function resolveColumns(columns: Column[], actual: string[]): Record<string, number> {
  const positions = new Map<string, number>();
  actual.forEach((name, index) => {
    const key = String(name).trim();
    if (!positions.has(key)) positions.set(key, index);
  });
  const resolved: Record<string, number> = {};
  for (const column of columns) {
    for (const name of [column.header, ...(column.aliases ?? [])]) {
      if (positions.has(name)) {
        resolved[column.header] = positions.get(name)!;
        break;
      }
    }
  }
  return resolved;
}

/**
 * Next occurrence of day-of-month `raw`. Today counts as due; a day past the
 * end of a short month lands on its last day rather than rolling over.
 */
export function nextDueFromDay(raw: unknown, now: Day): Day | null {
  const number = parseMoney(raw);
  if (number === null) return null;
  const wanted = Math.trunc(Math.min(Math.max(number, 1), 31));
  const { year, month } = parts(now);
  for (const start of [day(year, month, 1), addMonths(day(year, month, 1), 1)]) {
    const p = parts(start);
    const candidate = day(p.year, p.month, Math.min(wanted, daysInMonth(p.year, p.month)));
    if (candidate >= now) return candidate;
  }
  return null;
}

/**
 * Typed rows out of raw `values` (header row first).
 *
 * Fully blank rows are dropped; columns the tab lacks read as their empty
 * value, so downstream code can rely on every field existing.
 */
export function readTable<T>(tab: string, values: string[][], now: Day): T[] {
  const columns = SCHEMA[tab];
  if (!values || values.length < 2) return [];

  let header = values[0].map((cell) => String(cell).trim());
  let rows = values.slice(1)
    .map((row) => header.map((_, i) => String(row[i] ?? "")))
    .filter((row) => row.some((cell) => cell.trim() !== ""));

  ({ header, rows } = adapt(tab, header, rows, now));
  const positions = resolveColumns(columns, header);

  return rows.map((row) => {
    const record: Record<string, unknown> = {};
    for (const column of columns) {
      const index = positions[column.header];
      if (index === undefined && column.key === "active") {
        // A tab with no Active column is all switched on.
        record[column.key] = true;
        continue;
      }
      record[column.key] = coerce(index === undefined ? "" : row[index], column.kind);
    }
    return record as T;
  });
}

/**
 * Synthesize canonical columns a foreign layout stores differently.
 *
 * Only ever adds columns, so a workbook already using canonical names passes
 * through untouched.
 */
function adapt(tab: string, header: string[], rows: string[][], now: Day) {
  const columns = SCHEMA[tab];
  const have = resolveColumns(columns, header);
  const present = new Set(header);

  if (tab === TAB.recurring && have["Next Due"] === undefined && present.has(DUE_DAY_COLUMN)) {
    const source = header.indexOf(DUE_DAY_COLUMN);
    header = [...header, "Next Due"];
    rows = rows.map((row) => {
      const due = nextDueFromDay(row[source], now);
      return [...row, due === null ? "" : isoString(due)];
    });
  }

  if (
    tab === TAB.allocations &&
    have["Percent"] === undefined && have["Amount"] === undefined &&
    present.has(ALLOCATION_TYPE_COLUMN) && present.has(ALLOCATION_VALUE_COLUMN)
  ) {
    const kindAt = header.indexOf(ALLOCATION_TYPE_COLUMN);
    const valueAt = header.indexOf(ALLOCATION_VALUE_COLUMN);
    const activeAt = header.indexOf("active");
    header = [...header, "Percent", "Amount"];
    rows = rows
      // A switched-off allocation is not a standing rule.
      .filter((row) => activeAt < 0 || toBool(row[activeAt]))
      .map((row) => {
        const isPercent = row[kindAt].trim().toLowerCase() === "percent";
        // The half that does not apply is zero, not blank.
        return [...row, isPercent ? row[valueAt] : "0", isPercent ? "0" : row[valueAt]];
      });
  }
  return { header, rows };
}

function isoString(value: Day): string {
  const { year, month, day: date } = parts(value);
  return `${year}-${String(month).padStart(2, "0")}-${String(date).padStart(2, "0")}`;
}
