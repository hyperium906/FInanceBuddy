"use server";

import { revalidateTag } from "next/cache";
import { formatCurrency as fc, parseMoney } from "@/lib/money";
import { fetchProduct, type ProductInfo } from "@/lib/products";
import { SHEET_TAG } from "@/lib/server/data";
import { appendPlannerRow } from "@/lib/server/sheets";

/** Read a product page's name and price. Failure is an ordinary result, not an error. */
export async function readProduct(url: string): Promise<ProductInfo> {
  return fetchProduct(String(url ?? ""));
}

export type AddState = { status: "idle" } | { status: "ok" | "error"; message: string };

/** Add one item to the planner tab, above its Total row. */
export async function addWishlistItem(_prev: AddState, form: FormData): Promise<AddState> {
  const name = String(form.get("name") ?? "").trim();
  const price = parseMoney(form.get("price")) ?? 0;
  if (!name) return { status: "error", message: "It needs a name — the page could not supply one." };
  if (price <= 0) return { status: "error", message: "It needs a price above zero." };

  try {
    const row = await appendPlannerRow({
      name,
      price,
      category: String(form.get("category") ?? ""),
      priority: String(form.get("priority") ?? "Medium"),
      timeline: String(form.get("timeline") ?? "").trim(),
      notes: String(form.get("url") ?? "").trim(),
    });
    revalidateTag(SHEET_TAG, { expire: 0 });
    return { status: "ok", message: `Added ${name} at ${fc(price)} — row ${row} of your planner.` };
  } catch (error) {
    return { status: "error", message: (error as Error).message };
  }
}
