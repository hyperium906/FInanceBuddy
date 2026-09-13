/**
 * The charts. Two deliberate form choices:
 *
 * *"Where did it go" is a stacked bar, not a pie.* Eyes compare lengths on a
 * shared baseline far better than angles, and a pie of nine categories is
 * unreadable at the small end, where the interesting tail lives.
 *
 * *Categorical colour follows the entity in a fixed order and never cycles.*
 * Past seven series the tail folds into "Other" rather than reusing a hue.
 * Every chart ships a legend or direct labels, and every page a table view.
 */

import { formatCurrency } from "@/lib/money";

const CATEGORICAL = Array.from({ length: 8 }, (_, i) => `var(--cat-${i + 1})`);
const MAX_SERIES = 7;

export interface Slice {
  label: string;
  value: number;
}

function foldTail(items: Slice[], limit = MAX_SERIES): Slice[] {
  if (items.length <= limit) return items;
  const ordered = [...items].sort((a, b) => b.value - a.value);
  const tail = ordered.slice(limit).reduce((s, item) => s + item.value, 0);
  return [...ordered.slice(0, limit), { label: "Other", value: tail }];
}

/** One horizontal stacked bar: what made up the total. 2px surface gaps between segments. */
export function CompositionBar({ items, name = "Spent" }: { items: Slice[]; name?: string }) {
  const folded = foldTail(items.filter((item) => item.value > 0));
  const total = folded.reduce((s, item) => s + item.value, 0) || 1;
  const colour = (label: string, i: number) => (label === "Other" ? "var(--neutral)" : CATEGORICAL[i]);

  return (
    <figure className="space-y-3">
      <div className="flex h-12 w-full gap-[2px]" role="img" aria-label={`${name} by category`}>
        {folded.map((item, i) => (
          <div
            key={item.label}
            title={`${item.label}: ${formatCurrency(item.value)} (${((item.value / total) * 100).toFixed(1)}%)`}
            className="h-full first:rounded-l last:rounded-r"
            style={{ width: `${(item.value / total) * 100}%`, background: colour(item.label, i) }}
          />
        ))}
      </div>
      <figcaption className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {folded.map((item, i) => (
          <span key={item.label} className="inline-flex items-center gap-2">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: colour(item.label, i) }} aria-hidden />
            {item.label}
            <span className="tabular-nums text-[var(--muted)]">{formatCurrency(item.value)}</span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Horizontal bars, largest first, one hue: the job is magnitude, not identity. */
export function RankedBars({ items }: { items: Slice[] }) {
  const ordered = [...items].sort((a, b) => b.value - a.value);
  const max = Math.max(...ordered.map((item) => item.value), 1);
  return (
    <div className="space-y-2">
      {ordered.map((item) => (
        <div key={item.label} className="grid grid-cols-[minmax(6rem,10rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate">{item.label}</span>
          <div className="h-4" title={`${item.label}: ${formatCurrency(item.value)}`}>
            <div className="h-full rounded-r" style={{ width: `${(item.value / max) * 100}%`, background: "var(--cat-1)" }} />
          </div>
          <span className="tabular-nums text-[var(--muted)]">{formatCurrency(item.value)}</span>
        </div>
      ))}
    </div>
  );
}

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = Math.floor(min / step) * step; t <= max + step * 0.001; t += step) ticks.push(Math.round(t * 100) / 100);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/** Several measures of the same unit across checks — one axis, never two. */
export function TrendChart({ labels, series }: { labels: string[]; series: Array<{ name: string; values: number[] }> }) {
  const width = 640;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 28, left: 64 };
  const all = series.flatMap((s) => s.values);
  const ticks = niceTicks(Math.min(0, ...all), Math.max(...all, 0));
  const [lo, hi] = [ticks[0], ticks[ticks.length - 1]];
  const x = (i: number) => pad.left + (labels.length > 1 ? (i * (width - pad.left - pad.right)) / (labels.length - 1) : 0);
  const y = (v: number) => pad.top + ((hi - v) * (height - pad.top - pad.bottom)) / (hi - lo || 1);
  const ordered = [...series].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap gap-x-5 text-sm">
        {ordered.map((s, i) => (
          <span key={s.name} className="inline-flex items-center gap-2">
            <span className="inline-block h-[2px] w-4" style={{ background: CATEGORICAL[i] }} aria-hidden />
            {s.name}
          </span>
        ))}
      </figcaption>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full min-w-[480px]" role="img" aria-label="Checks ahead">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
              <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">
                {formatCurrency(t).replace(".00", "")}
              </text>
            </g>
          ))}
          {labels.map((label, i) => (
            <text key={label} x={x(i)} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{label}</text>
          ))}
          {ordered.map((s, si) => (
            <g key={s.name}>
              <polyline
                points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
                fill="none" stroke={CATEGORICAL[si]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
              />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={4} fill={CATEGORICAL[si]} stroke="var(--bg)" strokeWidth={2}>
                  <title>{`${labels[i]} · ${s.name}: ${formatCurrency(v)}`}</title>
                </circle>
              ))}
            </g>
          ))}
        </svg>
      </div>
    </figure>
  );
}
