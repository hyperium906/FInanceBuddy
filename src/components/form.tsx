import type { ReactNode } from "react";

export const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none";

export const primaryButton =
  "rounded-md bg-[var(--accent)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50";

export const secondaryButton =
  "rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--panel)] disabled:opacity-50";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-[var(--muted)]">{hint}</span>}
    </label>
  );
}
