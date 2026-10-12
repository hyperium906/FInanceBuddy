/**
 * Page furniture: cards, stat tiles, meters, status chips and callouts.
 * Server-renderable — nothing here holds state.
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

export function Card({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-[var(--border)] p-4 md:p-5 ${className}`}>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {aside && <div className="text-sm text-[var(--muted)]">{aside}</div>}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-[var(--muted)]">{children}</p>;
}

export function Stat({ label, value, note, alarm = false }: { label: string; value: ReactNode; note?: ReactNode; alarm?: boolean }) {
  return (
    <div className="rounded-lg bg-[var(--panel)] px-4 py-3">
      <div className="text-sm text-[var(--muted)]">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {note && <div className={`mt-1 text-xs ${alarm ? "text-[var(--bad)]" : "text-[var(--muted)]"}`}>{alarm && "▼ "}{note}</div>}
    </div>
  );
}

/**
 * One ratio against one limit: a track, a fill, and an overflow segment past a
 * 2px gap, so 110% reads as a tenth again rather than as a full bar.
 */
export function Meter({ share, over = 0, color = "var(--accent)", height = 10, label }: {
  share: number; over?: number; color?: string; height?: number; label?: string;
}) {
  const filled = Math.max(0, Math.min(share, 1)) * 100;
  const spill = Math.max(0, Math.min(over, 1)) * 100;
  return (
    <div
      role="meter" aria-label={label} aria-valuenow={Math.round((share + over) * 100)} aria-valuemin={0} aria-valuemax={100}
      className="flex w-full overflow-hidden rounded" style={{ height, background: "var(--track)" }}
    >
      <div className="rounded-l" style={{ width: `${filled}%`, background: color }} />
      {spill > 0 && (
        <>
          <div style={{ width: 2 }} />
          <div className="rounded-r" style={{ width: `${spill}%`, background: "var(--bad)", opacity: 0.75 }} />
        </>
      )}
    </div>
  );
}

/** A status glyph in its colour, the label in ink — colour never carries meaning alone. */
export function StatusChip({ status, children }: { status: Status; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium">
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
