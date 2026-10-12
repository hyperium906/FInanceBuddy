/**
 * "Where did it go" is a stacked bar, not a pie: lengths on a shared baseline
 * compare far better than angles, and the small tail stays readable.
 *
 * Categorical colour follows a fixed order and never cycles; past seven
 * series the tail folds into "Other". The legend carries every value, so the
 * chart never depends on colour-matching alone.
 */

import { formatCurrency } from "@/lib/money";

const CATEGORICAL = Array.from({ length: 8 }, (_, i) => `var(--cat-${i + 1})`);
const MAX_SERIES = 7;

export interface Slice {
  label: string;
  value: number;
}

function foldTail(items: Slice[], limit = MAX_SERIES): Slice[] {
  const ordered = [...items].sort((a, b) => b.value - a.value);
  if (ordered.length <= limit) return ordered;
  const tail = ordered.slice(limit).reduce((s, item) => s + item.value, 0);
  return [...ordered.slice(0, limit), { label: "Other", value: tail }];
}

export function CompositionBar({ items, name }: { items: Slice[]; name: string }) {
  const folded = foldTail(items.filter((item) => item.value > 0));
  const total = folded.reduce((s, item) => s + item.value, 0) || 1;
  const colour = (label: string, i: number) => (label === "Other" ? "var(--neutral)" : CATEGORICAL[i]);

  return (
    <figure className="space-y-3">
      <div className="flex h-8 w-full gap-[2px]" role="img" aria-label={name}>
        {folded.map((item, i) => (
          <div
            key={item.label}
            title={`${item.label}: ${formatCurrency(item.value)} (${((item.value / total) * 100).toFixed(1)}%)`}
            className="h-full first:rounded-l last:rounded-r"
            style={{ width: `${(item.value / total) * 100}%`, background: colour(item.label, i) }}
          />
        ))}
      </div>
      <figcaption className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
        {folded.map((item, i) => (
          <span key={item.label} className="flex items-center gap-2">
            <span className="inline-block h-3 w-3 shrink-0 rounded-sm" style={{ background: colour(item.label, i) }} aria-hidden />
            <span className="truncate">{item.label}</span>
            <span className="ml-auto tabular-nums text-[var(--muted)]">
              {formatCurrency(item.value)} · {((item.value / total) * 100).toFixed(0)}%
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
