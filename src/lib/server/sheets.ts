/**
 * Google Sheets read/write layer, over the REST API.
 *
 * Everything the app knows about the workbook goes through here. Writes are
 * guarded so they can only land on an underscore-prefixed data tab — or the
 * one hand-built tab explicitly allowed below — never on the Budget Sheet.
 *
 * Every write positions cells by the tab's **live** header row, read fresh,
 * never by schema order: a reordered or renamed column would otherwise send
 * every value one column across, silently, into the sheet.
 */

import { JWT } from "google-auth-library";
import { iso, type Day } from "../dates";
import { resolveColumns, SCHEMA, TAB, toBool, type Kind } from "../models";
import { parseMoney } from "../money";
import { getSheetConfig } from "./config";

export class SheetsError extends Error {
  name = "SheetsError";
}

const SCOPES = ["https://www.googleapis.com/auth/spreadsheets"];
const BASE = "https://sheets.googleapis.com/v4/spreadsheets";

/** Hand-built tabs the app may nonetheless append to. Only the wishlist planner. */
const APPENDABLE_TABS = new Set<string>([TAB.planner]);

let client: JWT | null = null;

function auth(): JWT {
  if (!client) {
    const { credentials } = getSheetConfig();
    client = new JWT({ email: credentials.client_email, key: credentials.private_key, scopes: SCOPES });
  }
  return client;
}

function describe(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: { error?: { message?: string } } } })?.response;
  const message = response?.data?.error?.message ?? (error as Error)?.message ?? String(error);
  if (response?.status === 403) {
    return `${message} — share the spreadsheet with ${getSheetConfig().credentials.client_email} as an editor.`;
  }
  return message;
}

async function api<T>(
  what: string,
  suffix: string,
  { method = "GET", body, query = [] }: { method?: string; body?: unknown; query?: Array<[string, string]> } = {},
): Promise<T> {
  const { sheetId } = getSheetConfig();
  const url = new URL(`${BASE}/${sheetId}${suffix}`);
  for (const [key, value] of query) url.searchParams.append(key, value);
  try {
    const response = await auth().request<T>({ url: url.toString(), method, data: body });
    return response.data;
  } catch (error) {
    throw new SheetsError(`${what}: ${describe(error)}`);
  }
}

// --------------------------------------------------------------------------
// A1 notation
// --------------------------------------------------------------------------

export const quote = (tab: string) => `'${tab.replace(/'/g, "''")}'`;

export function columnLetter(column: number): string {
  let out = "";
  for (let n = column; n > 0; n = Math.floor((n - 1) / 26)) out = String.fromCharCode(65 + ((n - 1) % 26)) + out;
  return out;
}

const a1 = (tab: string, row: number, column: number) => `${quote(tab)}!${columnLetter(column)}${row}`;

// --------------------------------------------------------------------------
// Reads
// --------------------------------------------------------------------------

export async function listTabs(): Promise<Array<{ title: string; sheetId: number }>> {
  const data = await api<{ sheets?: Array<{ properties: { title: string; sheetId: number } }> }>(
    "Could not open the spreadsheet", "", { query: [["fields", "sheets.properties(sheetId,title)"]] },
  );
  return (data.sheets ?? []).map((s) => s.properties);
}

/** Every cell of each tab as displayed strings, in one API call. */
export async function batchRead(tabs: string[]): Promise<string[][][]> {
  if (!tabs.length) return [];
  const data = await api<{ valueRanges?: Array<{ values?: string[][] }> }>(
    `Failed reading ${tabs.length} tab(s)`, "/values:batchGet",
    { query: tabs.map((tab) => ["ranges", quote(tab)] as [string, string]) },
  );
  return tabs.map((_, i) => data.valueRanges?.[i]?.values ?? []);
}

async function readTab(tab: string, range = ""): Promise<string[][]> {
  const data = await api<{ values?: string[][] }>(
    `Failed reading tab '${tab}'`, `/values/${encodeURIComponent(quote(tab) + range)}`,
  );
  return data.values ?? [];
}

// --------------------------------------------------------------------------
// Write primitives
// --------------------------------------------------------------------------

function guardWrite(tab: string): void {
  if (APPENDABLE_TABS.has(tab)) return;
  if (tab === TAB.report || !tab.startsWith("_")) {
    throw new SheetsError(`Refusing to write to tab '${tab}': only tabs prefixed with '_' are writable, and '${TAB.report}' is never written to.`);
  }
}

async function batchWrite(tab: string, data: Array<{ range: string; values: unknown[][] }>): Promise<void> {
  if (!data.length) return;
  await api(`Failed updating tab '${tab}'`, "/values:batchUpdate", {
    method: "POST",
    body: { valueInputOption: "USER_ENTERED", data },
  });
}

async function appendValues(tab: string, rows: unknown[][]): Promise<void> {
  await api(`Failed appending to tab '${tab}'`, `/values/${encodeURIComponent(`${quote(tab)}!A1`)}:append`, {
    method: "POST",
    query: [["valueInputOption", "USER_ENTERED"], ["insertDataOption", "INSERT_ROWS"]],
    body: { values: rows },
  });
}

/**
 * Render one value for the Sheets API, which parses input like a person typing.
 *
 * So a percentage goes in as `7%` (a bare 7 into a percent-formatted cell
 * reads as 700%), and text that would be taken as a formula — a bank
 * description starting `=`, `+`, `-` or `@` — is forced to stay text.
 */
function renderCell(value: unknown, kind: Kind): string | number {
  if (value === null || value === undefined || String(value).trim() === "") return "";
  switch (kind) {
    case "date": return typeof value === "number" ? iso(value as Day) : String(value);
    case "bool": return toBool(value) ? "TRUE" : "FALSE";
    case "percent": {
      const number = parseMoney(value);
      return number === null ? "" : `${number}%`;
    }
    case "money":
    case "number":
    case "int": return parseMoney(value) ?? "";
    default: {
      const text = String(value);
      return /^[=+\-@]/.test(text) ? `'${text}` : text;
    }
  }
}

/** Short, collision-resistant id: eight hex characters from a UUID. */
export function newId(prefix = ""): string {
  return `${prefix}${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
}

/** Position of canonical column `name` in a live header row, through its aliases; -1 when absent. */
function findColumn(header: string[], name: string, tab: string): number {
  return resolveColumns(SCHEMA[tab] ?? [], header)[name] ?? header.indexOf(name);
}

function indexOf(header: string[], name: string, tab: string): number {
  const position = findColumn(header, name, tab);
  if (position < 0) throw new SheetsError(`Tab '${tab}' has no '${name}' column (found ${JSON.stringify(header)}).`);
  return position;
}

// --------------------------------------------------------------------------
// Record writers
// --------------------------------------------------------------------------

/**
 * Append records (keyed by canonical header) to `tab` in one call. Columns the
 * sheet lacks are dropped rather than widening the tab mid-write; a blank id
 * column gets a generated id.
 */
export async function appendRecords(tab: string, records: Array<Record<string, unknown>>, idHeader?: string): Promise<number> {
  guardWrite(tab);
  if (!records.length) return 0;
  const columns = SCHEMA[tab];

  // Deliberately fresh, not cached: aligning to a stale header writes every
  // value one column across.
  const live = ((await readTab(tab, "!1:1"))[0] ?? []).map((cell) => String(cell).trim());
  let positions = live.length ? resolveColumns(columns, live) : {};
  if (!Object.keys(positions).length) {
    positions = Object.fromEntries(columns.map((column, index) => [column.header, index]));
  }
  // Keys the schema does not know are placed only where the live header names
  // them exactly — a foreign layout's `due_day`, say — and dropped otherwise.
  for (const key of new Set(records.flatMap((record) => Object.keys(record)))) {
    if (!(key in positions) && live.includes(key)) positions[key] = live.indexOf(key);
  }
  const width = Math.max(...Object.values(positions)) + 1;
  const kinds: Record<string, Kind> = Object.fromEntries(columns.map((column) => [column.header, column.kind]));

  const rows = records.map((record) => {
    const row: Array<string | number> = Array(width).fill("");
    for (const [header, index] of Object.entries(positions)) {
      let value = record[header];
      if (header === idHeader && (value === undefined || String(value).trim() === "")) value = newId();
      row[index] = renderCell(value, kinds[header] ?? "text");
    }
    return row;
  });
  await appendValues(tab, rows);
  return rows.length;
}

/** Find the row whose `matchHeader` equals `matchValue`, and set `updates` on it in one call. */
export async function updateRecord(
  tab: string,
  matchHeader: string,
  matchValue: string,
  updates: Record<string, unknown>,
  missingMessage: string,
  /** Fields to skip, rather than fail on, when the tab has no such column. */
  optional: string[] = [],
): Promise<void> {
  guardWrite(tab);
  const values = await readTab(tab);
  if (!values.length) throw new SheetsError(`Tab '${tab}' is empty (no header row).`);
  const header = values[0].map((cell) => String(cell).trim());
  const keyAt = indexOf(header, matchHeader, tab);
  const rowIndex = values.findIndex((row, i) => i > 0 && String(row[keyAt] ?? "").trim() === matchValue);
  if (rowIndex < 0) throw new SheetsError(`${missingMessage} (tab '${tab}')`);

  const kinds: Record<string, Kind> = Object.fromEntries((SCHEMA[tab] ?? []).map((column) => [column.header, column.kind]));
  const data: Array<{ range: string; values: unknown[][] }> = [];
  for (const [name, value] of Object.entries(updates)) {
    const position = findColumn(header, name, tab);
    if (position < 0) {
      if (optional.includes(name)) continue;
      indexOf(header, name, tab); // throws the explanatory error
    }
    data.push({ range: a1(tab, rowIndex + 1, position + 1), values: [[renderCell(value, kinds[name] ?? "text")]] });
  }
  await batchWrite(tab, data);
}

/** The only `_Transactions` fields the site may change. Date, amount and description are what a row is. */
export const EDITABLE_TRANSACTION_FIELDS = ["Category", "Notes", "Account ID"] as const;
export type TransactionEdit = Partial<Record<(typeof EDITABLE_TRANSACTION_FIELDS)[number], string>>;

/**
 * Apply per-row edits to `_Transactions` in one read and one write. Every id is
 * resolved before anything is written, so one missing row changes nothing.
 */
export async function updateTransactions(changes: Record<string, TransactionEdit>): Promise<number> {
  const tab = TAB.transactions;
  guardWrite(tab);
  const ids = Object.keys(changes).filter((id) => Object.keys(changes[id]).length);
  if (!ids.length) return 0;

  const values = await readTab(tab);
  if (!values.length) throw new SheetsError(`Tab '${tab}' is empty (no header row).`);
  const header = values[0].map((cell) => String(cell).trim());
  const idAt = indexOf(header, "Transaction ID", tab);
  const rows = new Map<string, number>();
  values.forEach((row, i) => {
    const key = String(row[idAt] ?? "").trim();
    // First occurrence wins: a duplicated id is a sheet problem, and editing both would compound it.
    if (i > 0 && key && !rows.has(key)) rows.set(key, i + 1);
  });
  const missing = ids.filter((id) => !rows.has(id));
  if (missing.length) {
    throw new SheetsError(
      `No ${tab} row(s) with Transaction ID ${missing.slice(0, 5).map((m) => `'${m}'`).join(", ")}${missing.length > 5 ? "…" : ""}. ` +
        "The sheet may have changed since it was read — refresh and try again.",
    );
  }

  const editable = new Set<string>(EDITABLE_TRANSACTION_FIELDS);
  const data = ids.flatMap((id) =>
    Object.entries(changes[id]).map(([field, value]) => {
      if (!editable.has(field)) {
        throw new SheetsError(`Refusing to edit '${field}' on ${tab}: only ${EDITABLE_TRANSACTION_FIELDS.join(", ")} may be changed from the app.`);
      }
      return { range: a1(tab, rows.get(id)!, indexOf(header, field, tab) + 1), values: [[renderCell(value, "text")]] };
    }),
  );
  await batchWrite(tab, data);
  return ids.length;
}

/**
 * Save a month of budgets in one read and at most two writes: existing
 * categories updated in place, new ones appended.
 */
export async function upsertBudgets(month: string, entries: Array<{ category: string; amount: number }>): Promise<number> {
  const tab = TAB.budgets;
  guardWrite(tab);
  const values = await readTab(tab);
  if (!values.length) throw new SheetsError(`Tab '${tab}' is empty (no header row).`);
  const header = values[0].map((cell) => String(cell).trim());
  const [monthAt, categoryAt, amountAt] = ["Month", "Category", "Amount"].map((name) => indexOf(header, name, tab));

  const existing = new Map<string, number>();
  values.forEach((row, i) => {
    if (i > 0 && String(row[monthAt] ?? "").trim() === month) existing.set(String(row[categoryAt] ?? "").trim(), i + 1);
  });

  const updates: Array<{ range: string; values: unknown[][] }> = [];
  const additions: Array<Record<string, unknown>> = [];
  for (const { category, amount } of entries) {
    const name = category.trim();
    if (!name) continue;
    if (existing.has(name)) {
      updates.push({ range: a1(tab, existing.get(name)!, amountAt + 1), values: [[amount]] });
    } else {
      // The apostrophe keeps "2026-09" a month label; typed bare, Sheets makes it a date.
      additions.push({ "Budget ID": newId("b"), Month: `'${month}`, Category: name, Amount: amount, Notes: "" });
    }
  }
  await batchWrite(tab, updates);
  if (additions.length) await appendRecords(tab, additions);
  return updates.length + additions.length;
}

/** Set balances and their Last Updated stamp for several `_Accounts` rows, in one read and one write. */
export async function updateAccountBalances(changes: Array<{ id: string; balance: number }>, stamp: string): Promise<number> {
  const tab = TAB.accounts;
  guardWrite(tab);
  if (!changes.length) return 0;
  const values = await readTab(tab);
  if (!values.length) throw new SheetsError(`Tab '${tab}' is empty (no header row).`);
  const header = values[0].map((cell) => String(cell).trim());
  const [idAt, balanceAt] = ["Account ID", "Balance"].map((name) => indexOf(header, name, tab));
  const stampAt = findColumn(header, "Last Updated", tab);

  const data: Array<{ range: string; values: unknown[][] }> = [];
  for (const { id, balance } of changes) {
    const rowIndex = values.findIndex((row, i) => i > 0 && String(row[idAt] ?? "").trim() === id);
    if (rowIndex < 0) throw new SheetsError(`No account with Account ID '${id}' (tab '${tab}').`);
    data.push({ range: a1(tab, rowIndex + 1, balanceAt + 1), values: [[balance]] });
    if (stampAt >= 0) data.push({ range: a1(tab, rowIndex + 1, stampAt + 1), values: [[stamp]] });
  }
  await batchWrite(tab, data);
  return changes.length;
}

const PLANNER_HEADERS = ["category", "item name", "priority", "estimated cost", "status"];

/** The row index holding the planner's table header, found rather than assumed. */
export function plannerHeaderRow(values: string[][]): number {
  return values.findIndex((row) => {
    const cells = new Set(row.map((cell) => String(cell).trim().toLowerCase()));
    return PLANNER_HEADERS.filter((name) => cells.has(name)).length >= 4;
  });
}

/**
 * Add one item to the hand-built `Wishlist & Purchases` tab.
 *
 * The item block ends at a "Total" row carrying its own SUM, so new items go
 * **above** it: appended below, they would fall outside that sum, and a
 * summary widened to include them would count the Total row too. Every range
 * reading the block is rewritten to match rather than trusting the
 * spreadsheet's own range expansion. Returns the row number written.
 */
export async function appendPlannerRow(item: {
  name: string; price: number; category?: string; priority?: string; timeline?: string; notes?: string;
}): Promise<number> {
  const tab = TAB.planner;
  guardWrite(tab);
  const values = await readTab(tab);
  const headerRow = plannerHeaderRow(values);
  if (headerRow < 0) {
    throw new SheetsError(`Could not find the item table's header in '${tab}'. It needs a row naming Category, Item Name, Priority, Estimated Cost and Status.`);
  }
  const header = values[headerRow].map((cell) => String(cell).trim().toLowerCase());
  const position: Record<string, number> = {};
  header.forEach((name, i) => { if (name && !(name in position)) position[name] = i; });

  const labelAt = position["category"] ?? 0;
  let totalRow = -1;
  for (let i = headerRow + 1; i < values.length; i++) {
    if (String(values[i][labelAt] ?? "").trim().toLowerCase() === "total") {
      totalRow = i + 1;
      break;
    }
  }

  let target: number;
  if (totalRow < 0) {
    target = values.length + 1;
    for (let i = headerRow + 1; i < values.length; i++) {
      if (!values[i].some((cell) => String(cell).trim())) {
        target = i + 1;
        break;
      }
    }
  } else {
    target = totalRow;
    const sheet = (await listTabs()).find((t) => t.title === tab);
    if (!sheet) throw new SheetsError(`Tab '${tab}' does not exist in the spreadsheet.`);
    await api(`Failed making room in '${tab}'`, ":batchUpdate", {
      method: "POST",
      body: { requests: [{ insertDimension: { range: { sheetId: sheet.sheetId, dimension: "ROWS", startIndex: target - 1, endIndex: target }, inheritFromBefore: false } }] },
    });
  }

  const cells: Record<string, string | number> = {
    category: item.category ?? "", "item name": item.name, priority: item.priority ?? "Medium",
    "target timeline": item.timeline ?? "", "estimated cost": item.price, status: "Planned", notes: item.notes ?? "",
  };
  const data = Object.entries(cells)
    .filter(([key]) => key in position)
    .map(([key, value]) => ({ range: a1(tab, target, position[key] + 1), values: [[value]] }));

  if (totalRow >= 0 && ["estimated cost", "item name", "status"].every((key) => key in position)) {
    const [first, last] = [headerRow + 2, target];
    const money = columnLetter(position["estimated cost"] + 1);
    const name = columnLetter(position["item name"] + 1);
    const status = columnLetter(position["status"] + 1);
    const block = (column: string) => `${column}${first}:${column}${last}`;
    const range = (cell: string) => `${quote(tab)}!${cell}`;
    data.push(
      { range: range(`${money}${target + 1}`), values: [[`=SUM(${block(money)})`]] },
      { range: range("B4"), values: [[`=SUM(${block(money)})`]] },
      { range: range("D4"), values: [[`=COUNTA(${block(name)})`]] },
      { range: range("F4"), values: [[`=SUMIF(${block(status)}, "Purchased", ${block(money)})`]] },
      { range: range("J4"), values: [[`=IFERROR(COUNTIF(${block(status)}, "Purchased")/COUNTA(${block(name)}), 0)`]] },
      { range: range("J5"), values: [[`=COUNTIF(${block(status)}, "Purchased") & " of " & COUNTA(${block(name)}) & " bought"`]] },
    );
  }

  await batchWrite(tab, data);
  return target;
}
