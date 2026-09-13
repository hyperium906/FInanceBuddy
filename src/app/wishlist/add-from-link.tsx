"use client";

import { useActionState, useState, useTransition } from "react";
import { Field, inputClass, primaryButton, secondaryButton } from "@/components/form";
import { Callout } from "@/components/ui";
import type { ProductInfo } from "@/lib/products";
import { addWishlistItem, readProduct, type AddState } from "./actions";

/**
 * Paste a product URL, read its name and price, add it to the list. Auto-fill
 * failing is ordinary — large retailers block automated readers — so a failed
 * read says why and leaves the fields for typing in by hand.
 */
export function AddFromLink({ categories, open = false }: { categories: string[]; open?: boolean }) {
  const [state, action, saving] = useActionState<AddState, FormData>(async (prev, form) => {
    const next = await addWishlistItem(prev, form);
    if (next.status === "ok") {
      setUrl("");
      setName("");
      setPrice("");
      setFound(null);
    }
    return next;
  }, { status: "idle" });
  const [reading, startReading] = useTransition();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [found, setFound] = useState<ProductInfo | null>(null);

  function read() {
    startReading(async () => {
      const info = await readProduct(url);
      setFound(info);
      if (info.name) setName(info.name);
      if (info.price !== null) setPrice(String(info.price));
    });
  }

  const partial = found && (found.name || found.price !== null);

  return (
    <details open={open} className="rounded-lg border border-[var(--border)] p-4">
      <summary className="cursor-pointer font-medium">Add something from a link</summary>
      <form action={action} className="mt-4 space-y-4">
        <Field label="Product URL" hint="The page is read for its name and price. Nothing is saved yet.">
          <div className="flex gap-2">
            <input name="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" className={inputClass} />
            <button type="button" onClick={read} disabled={!url.trim() || reading} className={secondaryButton}>
              {reading ? "Reading…" : "Read the page"}
            </button>
          </div>
        </Field>

        {found && (found.ok
          ? <Callout tone="success">Read from {found.source}.</Callout>
          : partial
            ? <Callout tone="warn">Partly read: {found.reason}</Callout>
            : <Callout>{found.reason} Large retailers usually block this — type the name and price in yourself.</Callout>)}

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <input name="name" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Price">
            <input name="price" type="number" min="0" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Category">
            <select name="category" defaultValue="Shopping" className={inputClass}>
              {categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Priority">
            <select name="priority" defaultValue="Medium" className={inputClass}>
              <option>High</option><option>Medium</option><option>Low</option>
            </select>
          </Field>
          <Field label="Wanted by" hint="Optional. A quarter is read as its last day.">
            <input name="timeline" placeholder="Q1 2027" className={inputClass} />
          </Field>
        </div>

        {state.status !== "idle" && <Callout tone={state.status === "ok" ? "success" : "error"}>{state.message}</Callout>}
        <button className={primaryButton} disabled={saving}>{saving ? "Adding…" : "Add to the list"}</button>
        <p className="text-xs text-[var(--muted)]">
          Items are added to your <code>Wishlist &amp; Purchases</code> tab, above its Total row, and every summary formula on that tab is widened to
          include them.
        </p>
      </form>
    </details>
  );
}
