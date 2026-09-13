/**
 * The Payday page — what to do with the check that just landed.
 *
 * The order is the order the questions get asked: how much came in, what has
 * to leave, what is left to spend per category, where the last one went, and
 * what the next few checks look like. Everything is measured against the pay
 * period, because the money arrives on a payday and has to last to the next.
 */

import { CompositionBar, RankedBars, TrendChart } from "@/components/charts";
import { withSheet } from "@/components/guard";
import { PeriodNav } from "@/components/period-nav";
import { Callout, Caption, DataTable, Details, Meter, Metric, Metrics, PageTitle, Section, StatusChip, StatusMeter } from "@/components/ui";
import { forCheck, monthAhead, running } from "@/lib/commitments";
import { format } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { currentPeriod, readAnchor, readCadence, shift } from "@/lib/periods";
import { paycheckAmount } from "@/lib/server/data";
import { categoryLines, spentByCategory, summarise } from "@/lib/spending";

export const metadata = { title: "Payday · FinanceBuddy" };

const ORDINAL_WORDS: Record<number, string> = { 1: "first", 2: "second", 3: "third" };

export default async function PaydayPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const offset = Math.min(Number.parseInt(String(params.p ?? "0"), 10) || 0, 0);

  return withSheet("Payday", (data) => {
    const { config, today, transactions } = data;
    const anchor = readAnchor(config);
    const cadence = readCadence(config);
    const paycheck = paycheckAmount(config);
    const period = shift(currentPeriod(anchor, cadence, today), offset);

    const check = forCheck(period, paycheck, data.recurring, data.allocations, anchor, cadence, transactions, today);
    const summary = summarise(transactions, period, check.committed);
    const plan = check.allocation!;
    const lines = categoryLines(transactions, data.budgets, period);
    const spent = spentByCategory(transactions, period);
    const threaded = running(monthAhead(anchor, paycheck, data.recurring, data.allocations, cadence, today, 4));
    const spentTotal = spent.reduce((s, row) => s + row.spent, 0);

    const budgeted = lines.filter((line) => line.budgeted);
    const unbudgeted = lines.filter((line) => !line.budgeted && line.spent > 0);
    const done = plan.moves.filter((move) => move.done).length;
    const unfunded = threaded.filter((r) => r.unfunded);
    const carried = threaded.filter((r) => r.needsCarry);

    return (
      <div>
        <PageTitle>Payday</PageTitle>
        <PeriodNav period={period} offset={offset} today={today} path="/payday" />

        <Section title="This check">
          <p>
            <strong>The {ORDINAL_WORDS[check.which] ?? `${check.which}th`} of {check.of} checks in {format(period.payday, "Month")}.</strong>
            {check.dominant && (
              <> Almost all of it is <strong>{check.dominant.name}</strong> ({fc(check.dominant.amount)}) — this is the rent check.</>
            )}
          </p>
          <Metrics>
            <Metric label="Paycheck" value={fc(check.paycheck)} />
            <Metric label="Bills" value={fc(check.billsTotal)} note={`${check.bills.length} due before ${format(period.end, "DD Mon")}`} />
            <Metric label="Transfers" value={fc(check.movesTotal)} note={check.which === 1 ? "first check of the month" : "percentage rules only"} />
            <Metric label="Left to live on" value={fc(check.left)} note={`${fc(check.perDay)}/day over ${period.days} days`} alarm={check.short} />
          </Metrics>
          {check.short && (
            <Callout tone="warn">
              <strong>This check is {fc(-check.left)} short.</strong> Its bills and transfers come to {fc(check.committed)} against{" "}
              {fc(check.paycheck)} coming in, so the difference has to be carried over from the previous check rather than found here.
            </Callout>
          )}
          <Caption>
            Spent so far this period: <strong>{fc(summary.spent)}</strong> (day {period.elapsed(today)} of {period.days}).
          </Caption>
        </Section>

        <Section
          title="What this check covers"
          caption="Each bill is charged whole to the last check before it is due — not split across checks, because that is not how any of them are paid."
        >
          {check.bills.length ? (
            <DataTable
              head={["Due", "What", "Category", "Amount"]}
              numeric={[3]}
              rows={check.bills.map((b) => [format(b.due, "Dow DD Mon"), b.name, b.category || "—", fc(b.amount)])}
            />
          ) : (
            <Callout>No bills fall before {format(period.end, "DD Mon")}.</Callout>
          )}
        </Section>

        <Section title="Where money still needs to move">
          {plan.moves.length ? (
            <>
              <Meter share={plan.due ? plan.moved / plan.due : 0} color={plan.complete ? "var(--good)" : "var(--cat-1)"} height={10} />
              <Caption>
                <strong>{done} of {plan.moves.length}</strong> moves made · {fc(plan.moved)} of {fc(plan.due)} ·{" "}
                <strong>{fc(plan.outstanding)}</strong> still to transfer
              </Caption>
              <ul className="divide-y divide-[var(--border)]">
                {plan.moves.map((move) => (
                  <li key={move.rule.bucket} className="grid grid-cols-[1fr_auto_8rem] items-center gap-3 py-2">
                    <div>
                      <span aria-hidden>{move.done ? "✅" : "⬜"}</span> <strong>{move.rule.bucket}</strong>
                      <div className="text-xs text-[var(--muted)]">
                        {move.rule.kind === "percent" ? `${move.rule.rate}% of this check` : `${fc(move.rule.monthly)}/month`}
                        {move.rule.accountId && ` → ${move.rule.accountId}`}
                      </div>
                    </div>
                    <div className="text-right tabular-nums">{fc(move.due)}</div>
                    <div className="text-right text-sm text-[var(--muted)]">{move.done ? "moved" : `${fc(move.outstanding)} to go`}</div>
                  </li>
                ))}
              </ul>
              {plan.unallocated < 0 ? (
                <Callout tone="error">
                  The rules claim {fc(plan.due)} from a {fc(plan.paycheck)} check — <strong>{fc(-plan.unallocated)} more than it holds.</strong>
                </Callout>
              ) : (
                <Caption>
                  Leaves <strong>{fc(plan.unallocated)}</strong> of the check once every rule is satisfied. That is what the spending below comes out of.
                </Caption>
              )}
            </>
          ) : (
            <Callout>No standing rules in <code>_Allocations</code> yet.</Callout>
          )}
        </Section>

        <Section
          title="Left to spend"
          caption="Each budget is a monthly figure divided across the checks that month actually holds — 26 a year, not two a month — so these are this period's share, not the whole month's."
        >
          {lines.length ? (
            <>
              {budgeted.map((line) => {
                const pace = line.pace(period, today);
                return (
                  <div key={line.category} className="grid gap-x-6 gap-y-1 md:grid-cols-[3fr_2fr]">
                    <div className="space-y-1">
                      <div className="flex items-center gap-3">
                        <strong>{line.category}</strong>
                        <StatusChip status={line.status}>{Math.round((line.used ?? 0) * 100)}% used</StatusChip>
                      </div>
                      <StatusMeter spent={line.spent} allowance={line.allowance} status={line.status} />
                    </div>
                    <div className="text-sm text-[var(--muted)]">
                      <div>
                        <strong className="text-[var(--ink)]">{fc(line.spent)}</strong> of {fc(line.allowance)} ·{" "}
                        {line.left >= 0 ? <><strong className="text-[var(--ink)]">{fc(line.left)}</strong> left</> : <strong className="text-[var(--ink)]">{fc(-line.left)} over</strong>}
                      </div>
                      <div>{pace ? `${fc(pace)}/day for the rest of the period` : "nothing left for this period"}</div>
                    </div>
                  </div>
                );
              })}
              {unbudgeted.length > 0 && (
                <Caption>
                  <strong>No budget set:</strong> {unbudgeted.map((l) => `${l.category} ${fc(l.spent)}`).join(" · ")}
                </Caption>
              )}
            </>
          ) : (
            <Callout>Nothing spent yet this period, and no budgets set.</Callout>
          )}
        </Section>

        <Section title="Where it went">
          {spent.length ? (
            <>
              <Caption><strong>{fc(spentTotal)}</strong> spent across {spent.length} categories.</Caption>
              <CompositionBar items={spent.map((r) => ({ label: r.category, value: r.spent }))} />
              <RankedBars items={spent.map((r) => ({ label: r.category, value: r.spent }))} />
              <Details summary="As a table">
                <DataTable
                  head={["Category", "Spent", "Share"]}
                  numeric={[1, 2]}
                  rows={spent.map((r) => [r.category, fc(r.spent), `${((r.spent / spentTotal) * 100).toFixed(1)}%`])}
                />
              </Details>
            </>
          ) : (
            <Callout>Nothing spent in this period yet.</Callout>
          )}
        </Section>

        <Section title="The checks ahead">
          <DataTable
            head={["Check", "Of the month", "Carried in", "Bills", "Transfers", "Balance after", ""]}
            numeric={[2, 3, 4, 5]}
            rows={threaded.map((r) => [
              format(r.check.period.start, "DD Mon"),
              `${r.check.which} of ${r.check.of}`,
              fc(r.opening),
              fc(r.check.billsTotal),
              fc(r.check.movesTotal),
              fc(r.closing),
              r.unfunded ? "🚨 unfunded" : r.needsCarry ? `🏠 needs ${fc(r.needsCarry)} carried` : r.check.isRentCheck ? "🏠 rent" : "",
            ])}
          />
          {unfunded.length ? (
            <Callout tone="error">
              <strong>{fc(unfunded[0].unfunded)} unfunded</strong> by {format(unfunded[0].check.period.start, "DD Mon")} — the surplus from earlier checks does not stretch that far.
            </Callout>
          ) : carried.length ? (
            <Caption>
              The rent check never covers itself on this income, and is not meant to —{" "}
              <strong>{carried.map((r) => format(r.check.period.start, "DD Mon")).join(", ")}</strong> draw{" "}
              {fc(carried.reduce((s, r) => s + r.needsCarry, 0))} from the check before. Every check still ends in the black, closing at{" "}
              <strong>{fc(threaded[threaded.length - 1].closing)}</strong>. What it means in practice: do not spend the first check of a month down to nothing.
            </Caption>
          ) : null}
          <TrendChart
            labels={threaded.map((r) => format(r.check.period.start, "DD Mon"))}
            series={[
              { name: "Committed", values: threaded.map((r) => r.check.committed) },
              { name: "Paycheck", values: threaded.map((r) => r.check.paycheck) },
              { name: "Running balance", values: threaded.map((r) => r.closing) },
            ]}
          />
        </Section>
      </div>
    );
  });
}
