/**
 * Page furniture shared by every screen: stat tiles, meters, status chips,
 * callouts and tables. Server-renderable — nothing here holds state.
 */

import type { ReactNode } from "react";
import type { Status } from "@/lib/spending";

export const STATUS_COLOR: Record<Status, string> = {
  green: "var(--good)",
  amber: "var(--warn)",
  red: "var(--bad)",
  none: "var(--none)",
};

/** A glyph per status, so the state survives greyscale and colour-blindness. */
export const STATUS_GLYPH: Record<Status, string> = { green: "●", amber: "▲", red: "■", none: "○" };

export function PageTitle({ children, caption }: { children: ReactNode; caption?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-semibold">{children}</h1>
      {caption && <p className="mt-1 max-w-3xl text-sm text-[var(--muted)]">{caption}</p>}
    </header>
  );
}

export function Section({ title, caption, children }: { title: string; caption?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-t border-[var(--border)] py-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {caption && <p className="mt-1 max-w-3xl text-sm text-[var(--muted)]">{caption}</p>}
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

export function Caption({ children }: { children: ReactNode }) {
  return <p className="text-sm text-[var(--muted)]">{children}</p>;
}

export function Metrics({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{children}</div>;
}

export function Metric({ label, value, note, alarm = false }: { label: string; value: ReactNode; note?: ReactNode; alarm?: boolean }) {
  return (
    <div className="rounded-lg bg-[var(--panel)] px-4 py-3">
      <div className="text-sm text-[var(--muted)]">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {note && (
        <div className={`mt-1 text-xs ${alarm ? "text-[var(--bad)]" : "text-[var(--muted)]"}`}>
          {alarm && "▼ "}
          {note}
        </div>
      )}
    </div>
  );
}

/**
 * One ratio against one limit: a track, a fill, and an overflow segment. Over
 * budget is drawn as a distinct segment past the fill, so 110% reads as a
 * tenth again rather than as a full bar identical to exactly-on-budget.
 */
export function Meter({ share, over = 0, color = "var(--accent)", height = 12, label }: {
  share: number; over?: number; color?: string; height?: number; label?: string;
}) {
  const filled = Math.max(0, Math.min(share, 1)) * 100;
  const spill = Math.max(0, Math.min(over, 1)) * 100;
  return (
    <div
      role="meter"
      aria-valuenow={Math.round((share + over) * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="flex w-full overflow-hidden rounded"
      style={{ height, background: "var(--track)" }}
    >
      <div style={{ width: `${filled}%`, background: color }} className="rounded-l" />
      {spill > 0 && (
        <>
          <div style={{ width: 2 }} />
          <div style={{ width: `${spill}%`, background: "var(--bad)", opacity: 0.75 }} className="rounded-r" />
        </>
      )}
    </div>
  );
}

export function StatusMeter({ spent, allowance, status }: { spent: number; allowance: number; status: Status }) {
  if (allowance <= 0) return <Meter share={0} color="var(--neutral)" />;
  const share = spent / allowance;
  return <Meter share={Math.min(share, 1)} over={Math.max(share - 1, 0)} color={STATUS_COLOR[status]} label={`${Math.round(share * 100)}% used`} />;
}

/** A status glyph in its colour, with the label in ink — colour never carries meaning alone. */
export function StatusChip({ status, children }: { status: Status; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm font-medium">
      <span style={{ color: STATUS_COLOR[status] }} aria-hidden>{STATUS_GLYPH[status]}</span>
      {children}
    </span>
  );
}

const TONES = {
  info: "border-[var(--cat-1)]",
  success: "border-[var(--good)]",
  warn: "border-[var(--warn)]",
  error: "border-[var(--bad)]",
};

export function Callout({ tone = "info", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <div className={`rounded-md border-l-4 bg-[var(--panel)] px-4 py-3 text-sm ${TONES[tone]}`}>{children}</div>;
}

export function DataTable({ head, rows, numeric = [] }: { head: string[]; rows: ReactNode[][]; numeric?: number[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="w-full text-sm">
        <thead className="bg-[var(--panel)] text-left text-[var(--muted)]">
          <tr>
            {head.map((name, i) => (
              <th key={i} className={`px-3 py-2 font-medium ${numeric.includes(i) ? "text-right" : ""}`}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-t border-[var(--border)]">
              {row.map((cell, i) => (
                <td key={i} className={`px-3 py-2 ${numeric.includes(i) ? "text-right tabular-nums" : ""}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Details({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="rounded-lg border border-[var(--border)] px-3 py-2">
      <summary className="cursor-pointer text-sm text-[var(--muted)]">{summary}</summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}
