/**
 * Every tab the app needs, read in one cached pass.
 *
 * Google allows roughly 60 reads a minute, so the raw cell values are cached
 * for five minutes under one tag, and every write expires that tag. What is
 * cached is the strings, not the parsed rows: parsing depends on today (a
 * `due_day` becomes a date), and a cached "today" would be wrong by morning.
 */

import { unstable_cache } from "next/cache";
import { mergeRules, type Rule } from "../categorize";
import { today as currentDay, type Day } from "../dates";
import {
  CONFIG_KEY_ALIASES, readTable, TAB,
  type Account, type Allocation, type Budget, type GoalRow, type Recurring, type RuleRow, type Transaction, type WishlistItem,
} from "../models";
import { parseMoney } from "../money";
import { PAYCHECK_KEY } from "../periods";
import type { PlannerItem } from "../wishlist";
import { batchRead, listTabs, plannerHeaderRow, SheetsError } from "./sheets";

export const SHEET_TAG = "sheet";

const REQUIRED_TABS = [TAB.transactions, TAB.accounts, TAB.recurring, TAB.budgets, TAB.allocations, TAB.goals, TAB.wishlist, TAB.config];
const OPTIONAL_TABS = [TAB.planner, TAB.rules];

export interface SheetData {
  today: Day;
  transactions: Transaction[];
  accounts: Account[];
  recurring: Recurring[];
  budgets: Budget[];
  allocations: Allocation[];
  goals: GoalRow[];
  wishlist: WishlistItem[];
  planner: PlannerItem[];
  config: Record<string, string>;
  /** Shipped rules merged with those learned into `_Rules`. */
  rules: Rule[];
  /** Whether `_Rules` exists, so the Import page can say where learning goes. */
  hasRulesTab: boolean;
}

const readRaw = unstable_cache(
  async (): Promise<Record<string, string[][]>> => {
    const titles = new Set((await listTabs()).map((tab) => tab.title));
    const missing = REQUIRED_TABS.filter((tab) => !titles.has(tab));
    if (missing.length) {
      throw new SheetsError(`Missing tab${missing.length > 1 ? "s" : ""} ${missing.map((t) => `'${t}'`).join(", ")} in the spreadsheet.`);
    }
    const tabs = [...REQUIRED_TABS, ...OPTIONAL_TABS.filter((tab) => titles.has(tab))];
    const values = await batchRead(tabs);
    return Object.fromEntries(tabs.map((tab, i) => [tab, values[i]]));
  },
  ["sheet-raw-v1"],
  { tags: [SHEET_TAG], revalidate: 300 },
);

function readConfig(values: string[][], now: Day): Record<string, string> {
  const settings: Record<string, string> = {};
  for (const row of readTable<{ key: string; value: string }>(TAB.config, values, now)) {
    if (row.key.trim()) settings[row.key.trim()] = row.value;
  }
  // An alias never shadows a key already present under its canonical name.
  for (const [canonical, aliases] of Object.entries(CONFIG_KEY_ALIASES)) {
    if (canonical in settings) continue;
    const alias = aliases.find((name) => name in settings);
    if (alias) settings[canonical] = settings[alias];
  }
  return settings;
}

/**
 * The hand-maintained planner, read directly rather than mirrored into a data
 * tab — copying it would create exactly the drift this workbook suffers
 * elsewhere. The header is found, because a planner laid out for humans has a
 * title and summary block above its table.
 */
function readPlanner(values: string[][] | undefined): PlannerItem[] {
  if (!values) return [];
  const start = plannerHeaderRow(values);
  if (start < 0) return [];
  const position: Record<string, number> = {};
  values[start].forEach((name, i) => {
    const key = String(name).trim().toLowerCase();
    if (key && !(key in position)) position[key] = i;
  });
  const pick = (row: string[], key: string) => (key in position ? String(row[position[key]] ?? "").trim() : "");

  const out: PlannerItem[] = [];
  for (const row of values.slice(start + 1)) {
    const name = pick(row, "item name");
    // The block's own Total row is arithmetic, not an item.
    if (!name || pick(row, "category").toLowerCase() === "total") continue;
    out.push({
      category: pick(row, "category"),
      name,
      priority: pick(row, "priority"),
      timeline: pick(row, "target timeline"),
      price: parseMoney(pick(row, "estimated cost")) ?? 0,
      status: pick(row, "status") || "Planned",
      notes: pick(row, "notes"),
      url: "",
      source: "planner",
    });
  }
  return out;
}

export async function loadData(): Promise<SheetData> {
  const raw = await readRaw();
  const now = currentDay();
  const read = <T>(tab: string) => readTable<T>(tab, raw[tab] ?? [], now);
  return {
    today: now,
    transactions: read<Transaction>(TAB.transactions),
    accounts: read<Account>(TAB.accounts),
    recurring: read<Recurring>(TAB.recurring),
    budgets: read<Budget>(TAB.budgets),
    allocations: read<Allocation>(TAB.allocations),
    goals: read<GoalRow>(TAB.goals),
    wishlist: read<WishlistItem>(TAB.wishlist),
    planner: readPlanner(raw[TAB.planner]),
    config: readConfig(raw[TAB.config] ?? [], now),
    rules: mergeRules(read<RuleRow>(TAB.rules)),
    hasRulesTab: TAB.rules in raw,
  };
}

/** Take-home per check from `_Config`, or 0 when unset. */
export function paycheckAmount(config: Record<string, string>): number {
  return parseMoney(config[PAYCHECK_KEY] || config["paycheck_net"]) ?? 0;
}
