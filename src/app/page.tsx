/**
 * The dashboard — the whole picture on one screen, for the pay period in
 * progress: what this check has to carry, what is about to leave, where the
 * spending stands, and what has been put aside.
 *
 * Everything is measured against the pay period, not the calendar month,
 * because the money arrives on a payday and has to last until the next one.
 */

import { CompositionBar } from "@/components/charts";
import { withSheet } from "@/components/guard";
import { Callout, Card, Meter, Muted, PageTitle, Stat, STATUS_COLOR, StatusChip } from "@/components/ui";
import { budgetedAmounts, observe } from "@/lib/billing";
import { forCheck, monthAhead, running } from "@/lib/commitments";
import { format } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { currentPeriod, perPeriod, readAnchor, readCadence } from "@/lib/periods";
import { upcoming } from "@/lib/recurring";
import { buckets, planFor, readGoals, retirementRate, retirementTotal, savedTotal, savingsRate, untracked, type GoalVerdict } from "@/lib/savings";
import { paycheckAmount } from "@/lib/server/data";
import { categoryLines, spentByCategory, summarise, type Status } from "@/lib/spending";

export const metadata = { title: "Dashboard · FinanceBuddy" };

const GOAL_STATUS: Record<GoalVerdict, Status> = { met: "green", "on-track": "green", stretch: "amber", missing: "red", "no-date": "none" };
const GOAL_WORDS: Record<GoalVerdict, string> = { met: "funded", "on-track": "on track", stretch: "a stretch", missing: "out of reach", "no-date": "no date" };
const ORDINAL_WORDS: Record<number, string> = { 1: "first", 2: "second", 3: "third" };

/** How far ahead "coming up" looks: the rest of a fortnight. */
const HORIZON_DAYS = 13;

export default async function Dashboard() {
  return withSheet("Dashboard", (data) => {
    const { config, today, transactions, recurring, allocations } = data;
    const anchor = readAnchor(config);
    const cadence = readCadence(config);
    const paycheck = paycheckAmount(config);
    const period = currentPeriod(anchor, cadence, today);

    // This check
    const check = forCheck(period, paycheck, recurring, allocations, anchor, cadence, transactions, today);
    const plan = check.allocation!;
    const spentSoFar = summarise(transactions, period, check.committed).spent;
    const threaded = running(monthAhead(anchor, paycheck, recurring, allocations, cadence, today, 4));
    const next = threaded[1];
    const usedShare = check.left > 0 ? spentSoFar / check.left : spentSoFar > 0 ? 2 : 0;
    const usedColour = usedShare > 1 ? "var(--bad)" : usedShare >= 0.8 ? "var(--warn)" : "var(--accent)";

    // Coming up — metered bills at the dearest charge seen, like the check itself
    const metered = budgetedAmounts(recurring, observe(recurring, transactions, today));
    const soon = upcoming(recurring, today, HORIZON_DAYS).map((u) => ({ ...u, amount: metered[u.name] ?? u.amount }));
    const soonTotal = soon.reduce((s, u) => s + u.amount, 0);
    const done = plan.moves.filter((m) => m.done).length;

    // Spending
    const lines = categoryLines(transactions, data.budgets, period);
    const budgeted = lines.filter((l) => l.budgeted);
    const unbudgeted = lines.filter((l) => !l.budgeted && l.spent > 0);
    const spent = spentByCategory(transactions, period);

    // Savings & balances
    const pots = buckets(allocations, data.accounts, paycheck, cadence);
    const spare = Math.max(threaded.reduce((s, r) => s + r.check.left, 0) / threaded.length, 0);
    const goals = readGoals(data.goals).map((goal) => planFor(goal, anchor, spare, perPeriod(savingsRate(pots), cadence), cadence, today));
    const accounts = data.accounts.filter((a) => a.id).sort((a, b) => b.balance - a.balance);
    const missing = untracked(pots);

    return (
      <div className="space-y-6">
        <PageTitle
          caption={
            <>
              {format(today, "Dow DD Month YYYY")} · pay period <strong>{period.label}</strong>, day {period.elapsed(today)} of {period.days} ·{" "}
              {period.remaining(today)} days left
            </>
          }
        >
          Dashboard
        </PageTitle>

        <Card
          title="This check"
          aside={<>The {ORDINAL_WORDS[check.which] ?? `${check.which}th`} of {check.of} in {format(period.payday, "Month")}</>}
        >
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Paycheck" value={fc(check.paycheck)} note={`landed ${format(period.payday, "Dow DD Mon")}`} />
            <Stat label="Bills" value={fc(check.billsTotal)} note={`${check.bills.length} due by ${format(period.end, "DD Mon")}`} />
            <Stat label="Transfers" value={fc(check.movesTotal)} note={check.which === 1 ? "first check of the month" : "percentage rules only"} />
            <Stat label="Left to live on" value={fc(check.left)} note={`${fc(check.perDay)}/day over ${period.days} days`} alarm={check.short} />
          </div>
          <div className="space-y-1">
            <div className="flex flex-wrap justify-between gap-2 text-sm">
              <span>
                <strong>{fc(spentSoFar)}</strong> spent so far
              </span>
              <span className="text-[var(--muted)]">
                {check.left - spentSoFar >= 0 ? `${fc(check.left - spentSoFar)} still free this check` : `${fc(spentSoFar - check.left)} past what this check had`}
              </span>
            </div>
            <Meter share={Math.min(usedShare, 1)} over={Math.max(usedShare - 1, 0)} color={usedColour} label="Share of this check's spending money used" />
          </div>
          {check.dominant && (
            <Callout tone={check.short ? "warn" : "info"}>
              This is the rent check — <strong>{check.dominant.name}</strong> takes {fc(check.dominant.amount)} of it.
              {check.short && <> Its bills and transfers come to {fc(check.committed)}, so {fc(-check.left)} has to be carried over from the check before.</>}
            </Callout>
          )}
          {next && (
            <Muted>
              Next check {format(next.check.period.start, "Dow DD Mon")}: {fc(next.check.left)} left after its own bills and transfers
              {next.check.isRentCheck && " — the rent check"}
              {next.needsCarry > 0 && <>, and it needs <strong>{fc(next.needsCarry)}</strong> carried in from this one. Don&apos;t spend this check down to nothing.</>}
              {next.unfunded > 0 && <> — <strong>{fc(next.unfunded)} unfunded</strong>.</>}
              {!next.needsCarry && !next.unfunded && "."}
            </Muted>
          )}
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Coming up" aside={`next ${HORIZON_DAYS + 1} days · ${fc(soonTotal)}`}>
            {soon.length ? (
              <ul className="divide-y divide-[var(--border)]">
                {soon.map((u) => (
                  <li key={`${u.name}-${u.due}`} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 py-2 text-sm">
                    <span className="text-[var(--muted)]">{format(u.due, "Dow DD Mon")}</span>
                    <span>
                      {u.name}
                      <span className="block text-xs text-[var(--muted)]">{u.daysAway === 0 ? "today" : `in ${u.daysAway} day${u.daysAway === 1 ? "" : "s"}`}</span>
                    </span>
                    <span className="tabular-nums">{fc(u.amount)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Muted>No bills in the next {HORIZON_DAYS + 1} days.</Muted>
            )}

            <div className="space-y-2 border-t border-[var(--border)] pt-4">
              <div className="flex flex-wrap justify-between gap-2 text-sm">
                <strong>Transfers to make</strong>
                <span className="text-[var(--muted)]">
                  {done} of {plan.moves.length} done · {fc(plan.outstanding)} to go
                </span>
              </div>
              {plan.moves.length ? (
                <>
                  <Meter share={plan.due ? plan.moved / plan.due : 0} color={plan.complete ? "var(--good)" : "var(--cat-1)"} label="Transfers made" />
                  <ul className="space-y-1 text-sm">
                    {plan.moves.map((move) => (
                      <li key={move.rule.bucket} className="flex justify-between gap-3">
                        <span>
                          <span aria-hidden>{move.done ? "✅" : "⬜"}</span> {move.rule.bucket}
                          {move.rule.accountId && <span className="text-xs text-[var(--muted)]"> → {move.rule.accountId}</span>}
                        </span>
                        <span className={`tabular-nums ${move.done ? "text-[var(--muted)]" : ""}`}>{move.done ? "moved" : fc(move.outstanding)}</span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <Muted>No standing rules in _Allocations.</Muted>
              )}
            </div>
          </Card>

          <Card title="Spending" aside={`this check · ${fc(spentSoFar)}`}>
            {budgeted.length ? (
              <ul className="space-y-3">
                {budgeted.map((line) => (
                  <li key={line.category} className="space-y-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                      <span className="flex items-center gap-2">
                        {line.category}
                        <StatusChip status={line.status}>{Math.round((line.used ?? 0) * 100)}%</StatusChip>
                      </span>
                      <span className="tabular-nums text-[var(--muted)]">
                        {fc(line.spent)} of {fc(line.allowance)} ·{" "}
                        {line.left >= 0 ? <span className="text-[var(--ink)]">{fc(line.left)} left</span> : <span className="text-[var(--bad)]">{fc(-line.left)} over</span>}
                      </span>
                    </div>
                    <Meter
                      share={Math.min(line.spent / line.allowance, 1)}
                      over={Math.max(line.spent / line.allowance - 1, 0)}
                      color={STATUS_COLOR[line.status]}
                      label={`${line.category} budget used`}
                    />
                  </li>
                ))}
              </ul>
            ) : (
              <Muted>No budgets set in _Budgets.</Muted>
            )}
            {unbudgeted.length > 0 && (
              <Muted>
                <strong>No budget:</strong> {unbudgeted.map((l) => `${l.category} ${fc(l.spent)}`).join(" · ")}
              </Muted>
            )}
            <div className="space-y-2 border-t border-[var(--border)] pt-4">
              <strong className="text-sm">Where it went</strong>
              {spent.length ? (
                <CompositionBar items={spent.map((r) => ({ label: r.category, value: r.spent }))} name="This check's spending by category" />
              ) : (
                <Muted>Nothing spent yet this check.</Muted>
              )}
            </div>
          </Card>
        </div>

        <Card title="Savings & balances">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Saved so far" value={fc(savedTotal(pots))} note={missing.length ? `${missing.length} bucket(s) not tracked` : "all buckets tracked"} />
            <Stat label="Going in" value={`${fc(savingsRate(pots))}/mo`} note={`${fc(perPeriod(savingsRate(pots), cadence))} a check`} />
            <Stat label="Spare on top" value={`${fc(spare)}/check`} note="averaged over the next 4 checks" />
            <Stat label="Retirement" value={fc(retirementTotal(pots))} note={`${fc(retirementRate(pots))}/mo — not reachable`} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <strong className="text-sm">Accounts</strong>
              {accounts.length ? (
                <ul className="mt-2 divide-y divide-[var(--border)]">
                  {accounts.map((a) => (
                    <li key={a.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <span>
                        {a.name || a.id}
                        <span className="block text-xs text-[var(--muted)]">
                          {[a.institution, a.type].filter(Boolean).join(" · ")}
                          {a.lastUpdated !== null && ` · updated ${format(a.lastUpdated, "DD Mon")}`}
                        </span>
                      </span>
                      <span className="tabular-nums">{fc(a.balance)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Muted>No accounts in _Accounts.</Muted>
              )}
            </div>

            <div>
              <strong className="text-sm">Goals</strong>
              {goals.length ? (
                <ul className="mt-2 space-y-4">
                  {goals.map((g) => (
                    <li key={g.goal.name} className="space-y-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                        <span className="flex items-center gap-2">
                          {g.goal.name}
                          <StatusChip status={GOAL_STATUS[g.verdict]}>{GOAL_WORDS[g.verdict]}</StatusChip>
                        </span>
                        <span className="tabular-nums text-[var(--muted)]">
                          {fc(g.goal.saved)} of {fc(g.goal.target)}
                        </span>
                      </div>
                      <Meter share={g.goal.progress} color={STATUS_COLOR[GOAL_STATUS[g.verdict]]} label={`${g.goal.name} progress`} />
                      {!g.goal.met && (
                        <Muted>
                          {g.goal.deadline !== null
                            ? `${fc(g.requiredPerCheck)} a check over ${g.checksLeft} checks to reach it by ${format(g.goal.deadline, "DD Mon YYYY")}`
                            : `${fc(g.goal.remaining)} to go, no date set`}
                        </Muted>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <Muted>No goals in _Goals yet.</Muted>
              )}
            </div>
          </div>
        </Card>
      </div>
    );
  });
}
