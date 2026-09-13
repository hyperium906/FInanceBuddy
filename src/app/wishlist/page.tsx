/**
 * The Wishlist page — what is wanted, and what can actually be paid for.
 *
 * Judging each item alone against the same pot turns thirty-seven wants into
 * thirty-five permissions to spend. So the headline figure is what fits
 * **together**, in priority order, with each item's own verdict beside it.
 */

import { CompositionBar, RankedBars } from "@/components/charts";
import { withSheet } from "@/components/guard";
import { Callout, Caption, DataTable, Details, Meter, Metric, Metrics, PageTitle, Section, STATUS_GLYPH, STATUS_COLOR, StatusChip } from "@/components/ui";
import { CATEGORIES } from "@/lib/categorize";
import { monthAhead, running } from "@/lib/commitments";
import { formatCurrency as fc } from "@/lib/money";
import { currentPeriod, readAnchor, readCadence } from "@/lib/periods";
import { paycheckAmount } from "@/lib/server/data";
import { spentIn, type Status } from "@/lib/spending";
import { assess, combine, headroom, summarise, type VerdictStatus } from "@/lib/wishlist";
import { AddFromLink } from "./add-from-link";

export const metadata = { title: "Wishlist · FinanceBuddy" };

const VERDICT_STATUS: Record<VerdictStatus, Status> = { yes: "green", tight: "amber", wait: "none", "out-of-reach": "red" };
const VERDICT_LABEL: Record<VerdictStatus, string> = { yes: "buy", tight: "tight", wait: "wait", "out-of-reach": "out of reach" };
const WISH_CATEGORIES = [...new Set([...CATEGORIES, "Shopping"])].sort();

export default async function WishlistPage() {
  return withSheet("Wishlist", (data) => {
    const planner = combine(data.planner, data.wishlist);
    if (!planner.length) {
      return (
        <div>
          <PageTitle>Wishlist</PageTitle>
          <Callout>Nothing wanted yet. Add something from a link below.</Callout>
          <div className="mt-4"><AddFromLink categories={WISH_CATEGORIES} open /></div>
        </div>
      );
    }

    const { config, today } = data;
    const anchor = readAnchor(config);
    const cadence = readCadence(config);
    const paycheck = paycheckAmount(config);
    const threaded = running(monthAhead(anchor, paycheck, data.recurring, data.allocations, cadence, today, 4));

    // Three subtractions, and the third matters: money a later check already
    // needs is sitting in the account and is not spendable.
    const room = headroom(
      threaded[0].check.left,
      spentIn(data.transactions, currentPeriod(anchor, cadence, today)),
      threaded.slice(1).reduce((s, r) => s + r.needsCarry, 0),
      Math.max(threaded.reduce((s, r) => s + r.check.left, 0) / threaded.length, 0),
    );
    const assessed = assess(planner, room, today);
    const summary = summarise(assessed);
    const fits = assessed.filter((a) => a.fits);
    const fitsTotal = fits.reduce((s, a) => s + a.price, 0);
    const missing = assessed.filter((a) => a.misses);

    const byCategory = [...assessed.reduce((m, a) => m.set(a.category, (m.get(a.category) ?? 0) + a.price), new Map<string, number>())]
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
    const listTotal = byCategory.reduce((s, c) => s + c.value, 0) || 1;

    return (
      <div>
        <PageTitle>Wishlist</PageTitle>
        <Metrics>
          <Metric label="Free to spend now" value={fc(room.available)} note={room.reserved ? `${fc(room.reserved)} held for later checks` : undefined} />
          <Metric label="Fits right now" value={`${summary.fitsCount} items`} note={fc(summary.fitsValue)} />
          <Metric label="On the list" value={`${summary.items} items`} note={fc(summary.total)} />
          <Metric label="Average price" value={fc(summary.average)} />
        </Metrics>
        <div className="mt-3">
          {room.overspent ? (
            <Callout tone="warn">This check is already past what it had to give, so nothing on the list is affordable out of it. The next one lands soon.</Callout>
          ) : summary.buyableNow > summary.fitsCount ? (
            <Caption>
              <strong>{summary.buyableNow} items would each fit on their own</strong>, but only <strong>{summary.fitsCount}</strong> fit together — buying one
              takes it out of what is left for the next. The list below is in priority order and stops where the money does.
            </Caption>
          ) : null}
        </div>

        <Section title="What you could buy today">
          {fits.length ? (
            <>
              <Meter share={room.available ? fitsTotal / room.available : 0} color="var(--good)" />
              <Caption>
                <strong>{fc(fitsTotal)}</strong> of {fc(room.available)} headroom, leaving {fc(room.available - fitsTotal)}.
              </Caption>
              <ul className="divide-y divide-[var(--border)]">
                {fits.map((item) => (
                  <li key={`${item.source}-${item.name}`} className="flex items-center justify-between gap-3 py-2">
                    <div className="flex flex-wrap items-center gap-3">
                      <strong>{item.name}</strong>
                      <StatusChip status={VERDICT_STATUS[item.verdict]}>{item.priority.toLowerCase() || VERDICT_LABEL[item.verdict]}</StatusChip>
                    </div>
                    <div className="text-right tabular-nums">
                      {fc(item.price)}
                      <div className="text-xs text-[var(--muted)]">running {fc(item.running)}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <Callout>Nothing on the list fits out of this check.</Callout>
          )}
        </Section>

        <Section title="Everything on the list">
          <DataTable
            head={["", "Item", "Category", "Price", "Verdict", "Link"]}
            numeric={[3]}
            rows={assessed.map((a) => [
              a.fits ? "✅" : "",
              <span key="n" title={a.note}>{a.name}</span>,
              a.category,
              fc(a.price),
              <span key="v"><span style={{ color: STATUS_COLOR[VERDICT_STATUS[a.verdict]] }} aria-hidden>{STATUS_GLYPH[VERDICT_STATUS[a.verdict]]}</span> {VERDICT_LABEL[a.verdict]}</span>,
              a.url ? <a key="l" href={a.url} target="_blank" rel="noreferrer" className="underline">open</a> : "",
            ])}
          />
          {missing.length > 0 && (
            <Callout tone="warn">
              <strong>{missing.length} item(s) would not be saved for in time</strong> at the current surplus:{" "}
              {missing.slice(0, 4).map((a) => `${a.name} (wanted ${a.timeline})`).join(", ")}
            </Callout>
          )}
        </Section>

        <Section title="Add something">
          <AddFromLink categories={WISH_CATEGORIES} />
        </Section>

        <Section title="Where the list's money is">
          <CompositionBar items={byCategory} name="Wishlist" />
          <RankedBars items={byCategory} />
          <Details summary="As a table">
            <DataTable
              head={["Category", "Total", "Share"]}
              numeric={[1, 2]}
              rows={byCategory.map((c) => [c.label, fc(c.value), `${((c.value / listTotal) * 100).toFixed(1)}%`])}
            />
          </Details>
        </Section>
      </div>
    );
  });
}
