"use server";

import { revalidateTag } from "next/cache";
import { SHEET_TAG } from "@/lib/server/data";

/** Drop the cached read, so the next render re-fetches the sheet. */
export async function refreshData(): Promise<void> {
  revalidateTag(SHEET_TAG, { expire: 0 });
}
