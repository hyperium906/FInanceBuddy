/**
 * Transaction categorization, tier 1: merchant scrubbing and rules.
 *
 * Rules map merchant substrings to categories. They cost nothing and handle
 * the recurring merchants that make up most of a statement. What they cannot
 * decide goes to the model (`server/categorizer.ts`), which is sent only a
 * scrubbed merchant name and the amount — never account numbers, and never a
 * raw description still carrying identifiers.
 *
 * When a category is corrected by hand, a rule is learned so that merchant
 * never needs the model again.
 */

import seed from "./category-rules.json";

/**
 * The category vocabulary, which is the workbook's rather than this module's.
 * `_Budgets`, `_Recurring`, and the Budget Sheet all key off these strings, so
 * a name emitted here that no budget uses is spending that lands unbudgeted.
 */
export const CATEGORIES = [
  "Groceries",
  "Dining",
  "Gas / Transportation",
  "Utilities",
  "Housing",
  "Subscriptions",
  "Giving",
  "Shopping",
  "Health",
  "Debt",
  // Money fronted for other people. Spent until repaid, so not a transfer; but
  // never yours, so it must not eat a budget. Repayments net against it.
  "Reimbursable",
  "Income",
  // Moving money between your own accounts. Not spending.
  "Transfer",
  "Other",
] as const;

export const UNCATEGORIZED = "Uncategorized";

export interface Rule {
  match: string;
  category: string;
}

// Ordered, and the order matters: labelled identifiers go first, because
// stripping bare digit runs ahead of them would strand the label ("ACCT 12345"
// -> "ACCT") and leak it into the prompt.
const SCRUB_PATTERNS: RegExp[] = [
  // Identifier labels, with or without a value following them.
  /\b(?:ref|id|acct|account|auth|trace|inv|invoice|conf|txn|trn)\b(?:\s*[:#]?\s*\S+)?/gi,
  // Masked card numbers: XXXX1234, ****5678. Token-anchored, so EXXON survives.
  /(?<![a-z0-9])[x*#]{2,}[\s-]*\d*(?![a-z])/gi,
  // Order/confirmation tokens mixing 2+ letters and 2+ digits. Keeps "7-eleven".
  /\b(?=[a-z0-9-]*[a-z][a-z0-9-]*[a-z])(?=[a-z0-9-]*\d[a-z0-9-]*\d)[a-z0-9-]{5,}\b/gi,
  // Any remaining run of 4+ digits — card, account, invoice, phone, trace.
  /\d{4,}/g,
  // Bank/rail noise that carries no merchant signal.
  /\b(?:pos|ach|eft|dbt|crd|debit|credit|purchase|payment|pmt|recurring|visa|mastercard|amex|withdrawal|deposit|sq|tst|py|dda|pur|xfer|ppd|des)\b/gi,
  // Dates left in the description.
  /\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b/g,
  // Store numbers: "#417", "STORE 22".
  /#\s*\d+/g,
  /\bstore\s+\d+\b/gi,
  // Trailing state code: "SAN FRANCISCO CA". Uppercase only, deliberately.
  /\b[A-Z]{2}\s*$/g,
];

/**
 * Reduce a raw bank description to a bare merchant name, with nothing
 * identifying left to send to a third-party model.
 *
 * `POS DEBIT TRADER JOE'S #417 XXXXXX4412 SAN JOSE CA` -> `trader joe's san jose`
 */
export function scrubMerchant(description: string, maxLength = 48): string {
  if (!description) return "";
  let text = String(description);
  for (const pattern of SCRUB_PATTERNS) text = text.replace(pattern, " ");
  text = text.replace(/[^\p{L}\p{N}_&'\-. ]+/gu, " ");
  text = text.replace(/\s+/g, " ").trim().toLowerCase();
  return text.slice(0, maxLength).trim();
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const patternCache = new Map<string, RegExp>();

/**
 * A rule pattern that will not match inside a longer word: "rent" must not
 * fire on "PARENT", nor "bp" on any merchant containing those letters.
 */
function patternFor(match: string): RegExp {
  let pattern = patternCache.get(match);
  if (!pattern) {
    pattern = new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRegExp(match.trim())}(?![\\p{L}\\p{N}_])`, "iu");
    patternCache.set(match, pattern);
  }
  return pattern;
}

/** Longest pattern first, so specific rules win. */
export function sortRules(rules: Rule[]): Rule[] {
  return [...rules].sort((a, b) => b.match.length - a.match.length);
}

export function cleanRules(entries: Array<Partial<Rule>>): Rule[] {
  return entries
    .map((entry) => ({ match: String(entry.match ?? "").trim().toLowerCase(), category: String(entry.category ?? "").trim() }))
    .filter((rule) => rule.match && rule.category);
}

/** The starter rules shipped with the app. */
export const SEED_RULES: Rule[] = cleanRules((seed as { rules: Array<Partial<Rule>> }).rules ?? []);

/**
 * Shipped rules plus those learned into the sheet. A learned rule for the same
 * merchant replaces the shipped one — the correction is the newer claim.
 */
export function mergeRules(learned: Rule[], shipped: Rule[] = SEED_RULES): Rule[] {
  const byMatch = new Map<string, Rule>();
  for (const rule of cleanRules(shipped)) byMatch.set(rule.match, rule);
  for (const rule of cleanRules(learned)) byMatch.set(rule.match, rule);
  return sortRules([...byMatch.values()]);
}

/** First rule whose pattern occurs in `merchant` as a whole token, or null. */
export function applyRules(merchant: string, rules: Rule[]): string | null {
  if (!merchant) return null;
  for (const rule of rules) {
    if (patternFor(rule.match).test(merchant)) return rule.category;
  }
  return null;
}

/**
 * Rules to learn from categories changed by hand: one per corrected row whose
 * scrubbed merchant is not already mapped to that category.
 */
export function learnFromEdits(
  rows: Array<{ description: string; category: string }>,
  baseline: string[],
  known: Rule[],
): Rule[] {
  const existing = new Map(known.map((rule) => [rule.match, rule.category]));
  const learned = new Map<string, Rule>();
  rows.forEach((row, index) => {
    const after = String(row.category ?? "").trim();
    const before = String(baseline[index] ?? "").trim();
    if (!after || after === before || after === UNCATEGORIZED) return;
    const merchant = scrubMerchant(row.description);
    if (!merchant || existing.get(merchant) === after) return;
    learned.set(merchant, { match: merchant, category: after });
  });
  return [...learned.values()];
}

/** Rows categorised by rule, and the ones left for the model. */
export function applyRuleTier<T extends { description: string; amount: number; category: string }>(
  rows: T[],
  rules: Rule[],
): { rows: T[]; ruleHits: number; pending: Array<{ index: number; merchant: string; amount: number }> } {
  let ruleHits = 0;
  const pending: Array<{ index: number; merchant: string; amount: number }> = [];
  const out = rows.map((row, index) => {
    if (String(row.category ?? "").trim()) return row;
    const merchant = scrubMerchant(row.description);
    const matched = applyRules(merchant, rules);
    if (matched) {
      ruleHits += 1;
      return { ...row, category: matched };
    }
    pending.push({ index, merchant: merchant || "unknown", amount: row.amount || 0 });
    return row;
  });
  return { rows: out, ruleHits, pending };
}
