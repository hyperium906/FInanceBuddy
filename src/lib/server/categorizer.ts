/**
 * Two-tier categorization: rules first, the model only for what is left.
 *
 * The model named by GEMINI_MODEL_FAST gets batches of scrubbed merchant names
 * and amounts, constrained to the category list by a response schema. A batch
 * that fails costs those rows a category, never the import.
 */

import { applyRuleTier, CATEGORIES, UNCATEGORIZED, type Rule } from "../categorize";
import { getGeminiConfig } from "./config";
import { generate, LLMError } from "./llm";

const BATCH_SIZE = 40;

const RESPONSE_SCHEMA = {
  type: "array",
  items: {
    type: "object",
    properties: {
      index: { type: "integer" },
      category: { type: "string", enum: [...CATEGORIES] },
    },
    required: ["index", "category"],
  },
};

function prompt(batch: Array<{ index: number; merchant: string; amount: number }>): string {
  return `You are categorizing personal bank transactions.

For each numbered item below, choose exactly one category from this list:
${CATEGORIES.join(", ")}

Rules:
- A positive amount is money coming in; a negative amount is money going out.
- Money coming in is almost always Income.
- Choose "Other" only when no other category plausibly fits.
- Return one object per item, echoing the item's index.

Items:
${batch.map((item) => `${item.index}. merchant=${JSON.stringify(item.merchant)} amount=${item.amount.toFixed(2)}`).join("\n")}`;
}

export interface CategorizeResult<T> {
  rows: T[];
  ruleHits: number;
  llmHits: number;
  unresolved: number;
  failures: string[];
}

export async function categorizeRows<T extends { description: string; amount: number; category: string }>(
  input: T[],
  rules: Rule[],
  useLlm = true,
): Promise<CategorizeResult<T>> {
  const { rows, ruleHits, pending } = applyRuleTier(input, rules);
  const out = [...rows];
  const failures: string[] = [];
  let llmHits = 0;

  if (pending.length && useLlm) {
    let model: string;
    try {
      model = getGeminiConfig().modelFast;
    } catch (error) {
      failures.push((error as Error).message);
      model = "";
    }
    const valid = new Set<string>(CATEGORIES);
    for (let start = 0; model && start < pending.length; start += BATCH_SIZE) {
      const batch = pending.slice(start, start + BATCH_SIZE);
      const number = start / BATCH_SIZE + 1;
      try {
        const answer = await generate(prompt(batch), { schema: RESPONSE_SCHEMA, model });
        if (!Array.isArray(answer)) throw new LLMError("Expected a JSON array of results.");
        const wanted = new Set(batch.map((item) => item.index));
        for (const entry of answer as Array<{ index?: unknown; category?: unknown }>) {
          const index = Number(entry?.index);
          const category = String(entry?.category ?? "").trim();
          if (wanted.has(index) && valid.has(category) && !out[index].category) {
            out[index] = { ...out[index], category };
            llmHits += 1;
          }
        }
      } catch (error) {
        failures.push(`Batch ${number}: ${(error as Error).message}`);
      }
    }
  }

  let unresolved = 0;
  const filled = out.map((row) => {
    if (String(row.category ?? "").trim()) return row;
    unresolved += 1;
    return { ...row, category: UNCATEGORIZED };
  });
  return { rows: filled, ruleHits, llmHits, unresolved, failures };
}
