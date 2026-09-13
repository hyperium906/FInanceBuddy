import Link from "next/link";
import type { Day } from "@/lib/dates";
import type { PayPeriod } from "@/lib/periods";

/** Previous / current / next for the period being viewed, carried in `?p=`. */
export function PeriodNav({ period, offset, today, path }: { period: PayPeriod; offset: number; today: Day; path: string }) {
  const link = (to: number) => (to === 0 ? path : `${path}?p=${to}`);
  const button = "rounded-md border border-[var(--border)] px-3 py-1.5 text-sm text-center hover:bg-[var(--panel)]";
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3">
      <Link href={link(offset - 1)} className={button}>← Previous</Link>
      <div className="text-center">
        <div className="font-semibold">{period.label}</div>
        <div className="text-xs text-[var(--muted)]">
          {offset === 0
            ? `day ${period.elapsed(today)} of ${period.days} · ${period.remaining(today)} left`
            : <>{Math.abs(offset)} check(s) {offset < 0 ? "ago" : "ahead"} · <em>not the current period</em></>}
        </div>
      </div>
      {offset < 0
        ? <Link href={link(offset + 1)} className={button}>Next →</Link>
        : <span className={`${button} cursor-not-allowed opacity-40`} aria-disabled>Next →</span>}
    </div>
  );
}
